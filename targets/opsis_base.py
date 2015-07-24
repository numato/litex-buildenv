# Support for the Numato Opsis - The first HDMI2USB production board

from fractions import Fraction

from migen.fhdl.std import *
from migen.fhdl.specials import Keep
from migen.genlib.resetsync import AsyncResetSynchronizer
from migen.bus import wishbone
from migen.genlib.record import Record

from misoclib.mem.sdram.module import SDRAMModule
from misoclib.mem.sdram.phy import s6ddrphy
from misoclib.mem.sdram.core.lasmicon import LASMIconSettings
from misoclib.mem.flash import spiflash
from misoclib.soc import mem_decoder
from misoclib.soc.sdram import SDRAMSoC
from misoclib.com.liteeth.common import *
from misoclib.com.liteeth.phy.mii import LiteEthPHYMII
from misoclib.com.liteeth.core.mac import LiteEthMAC

# DDR3
class MT41J128M16(SDRAMModule):
    # MT41J128M16 - 16 Meg x 16 x 8 Banks
    # Clock Rate - 800 MHz
    # Decode the chip markings using http://www.micron.com/support/fbga
    #
    # +-------------+------------------+--------------------+-----------+----------+---------+
    # | Speed Grade | Data Rate (MT/s) | Target tRCD-tRP-CL | tRCD (ns) | tRP (ns) | CL (ns) |
    # +-------------+------------------+--------------------+-----------+----------+---------+
    # |    -093     |     2133         |      14-14-14      |   13.13   |  13.13   |  13.13  |
    # |    -107     |     1866         |      13-13-13      |   13.91   |  13.91   |  13.91  |
    # |    -125     |     1600         |      11-11-11      |   13.75   |  13.75   |  13.75  |
    # |    -15E     |     1333         |       9-9-9        |   13.5    |  13.5    |  13.5   |
    # |    -187E    |     1066         |       7-7-7        |   13.1    |  13.1    |  13.1   |
    # |    -25E     |      800         |       5-5-5        |   ????    |  ????    |  ????   |
    # +-------------+------------------+--------------------+-----------+----------+---------+
    #  * Faster parts are compatible with slower speeds, IE -093 can run at -187E speeds.
    #
    # +-------------------+----------------------+----------------------+-----------------------+
    # |         Parameter |     512 Meg x 4      |     256 Meg x 8      |     128 Meg x 16      |
    # +-------------------+----------------------+----------------------+-----------------------+
    # |           Marking |        512M4         |        256M8         |         128M16        |
    # |     Configuration | 64 Meg x 4 x 8 banks | 32 Meg x 8 x 8 banks | 16 Meg x 16 x 8 banks |
    # |     Refresh count |         8K           |          8K          |          8K           |
    # |    Row addressing |    32K (A[14:0])     |     32K (A[14:0])    |     16K (A[13:0])     |
    # |   Bank addressing |     8 (BA[2:0])      |      8 (BA[2:0])     |      8 (BA[2:0])      |
    # | Column addressing |    2K (A[11, 9:0])   |      1K (A[9:0])     |      1K (A[9:0])      |
    # |         Page size |         1KB          |          1KB         |          2KB          |
    # +-------------------+----------------------+----------------------+-----------------------+
    geom_settings = {
        "nbanks":   8,      #   8 banks
        "nrows":    16384,  # 16K (A[13:0])
        "ncols":    1024,   #  1K (A[9:0])
    }
    timing_settings = {
        "tRP":        15,
        "tRCD":       15,
        "tWR":        15,
        "tWTR":        2,
        "tREFI":    7800,
        "tRFC":       70,
    }

    def __init__(self, clk_freq):
        SDRAMModule.__init__(self, clk_freq, "DDR3", self.geom_settings,
            self.timing_settings)


class _CRG(Module):
    def __init__(self, platform, clk_freq):
        self.clock_domains.cd_sys = ClockDomain()
        self.clock_domains.cd_sdram_half = ClockDomain()
        self.clock_domains.cd_sdram_full_wr = ClockDomain()
        self.clock_domains.cd_sdram_full_rd = ClockDomain()
        self.clock_domains.cd_base50 = ClockDomain()

        self.clk4x_wr_strb = Signal()
        self.clk4x_rd_strb = Signal()

        f0 = 100*1000000
        clk100 = platform.request("clk100")
        clk100a = Signal()
        self.specials += Instance("IBUFG", i_I=clk100, o_O=clk100a)
        clk100b = Signal()
        self.specials += Instance("BUFIO2", p_DIVIDE=1,
                                  p_DIVIDE_BYPASS="TRUE", p_I_INVERT="FALSE",
                                  i_I=clk100a, o_DIVCLK=clk100b)
        f = Fraction(int(clk_freq), int(f0))
        n, m = f.denominator, f.numerator
        assert f0/n*m == clk_freq
        p = 8
        pll_lckd = Signal()
        pll_fb = Signal()
        pll = Signal(6)
        self.specials.pll = Instance("PLL_ADV", p_SIM_DEVICE="SPARTAN6",
                                     p_BANDWIDTH="OPTIMIZED", p_COMPENSATION="INTERNAL",
                                     p_REF_JITTER=.01, p_CLK_FEEDBACK="CLKFBOUT",
                                     i_DADDR=0, i_DCLK=0, i_DEN=0, i_DI=0, i_DWE=0, i_RST=0, i_REL=0,
                                     p_DIVCLK_DIVIDE=1, p_CLKFBOUT_MULT=m*p//n, p_CLKFBOUT_PHASE=0.,
                                     i_CLKIN1=clk100b, i_CLKIN2=0, i_CLKINSEL=1,
                                     p_CLKIN1_PERIOD=1e9/f0, p_CLKIN2_PERIOD=0.,
                                     i_CLKFBIN=pll_fb, o_CLKFBOUT=pll_fb, o_LOCKED=pll_lckd,
                                     o_CLKOUT0=pll[0], p_CLKOUT0_DUTY_CYCLE=.5,
                                     o_CLKOUT1=pll[1], p_CLKOUT1_DUTY_CYCLE=.5,
                                     o_CLKOUT2=pll[2], p_CLKOUT2_DUTY_CYCLE=.5,
                                     o_CLKOUT3=pll[3], p_CLKOUT3_DUTY_CYCLE=.5,
                                     o_CLKOUT4=pll[4], p_CLKOUT4_DUTY_CYCLE=.5,
                                     o_CLKOUT5=pll[5], p_CLKOUT5_DUTY_CYCLE=.5,
                                     p_CLKOUT0_PHASE=0., p_CLKOUT0_DIVIDE=p//4,  # sdram wr rd
                                     p_CLKOUT1_PHASE=0., p_CLKOUT1_DIVIDE=p//4,
                                     p_CLKOUT2_PHASE=270., p_CLKOUT2_DIVIDE=p//2,  # sdram dqs adr ctrl
                                     p_CLKOUT3_PHASE=250., p_CLKOUT3_DIVIDE=p//2,  # off-chip ddr
                                     p_CLKOUT4_PHASE=0., p_CLKOUT4_DIVIDE=p//1,
                                     p_CLKOUT5_PHASE=0., p_CLKOUT5_DIVIDE=p//1,  # sys
        )
        self.specials += Instance("BUFG", i_I=pll[5], o_O=self.cd_sys.clk)
        reset = ~platform.request("cpu_reset")
        self.clock_domains.cd_por = ClockDomain()
        por = Signal(max=1 << 11, reset=(1 << 11) - 1)
        self.sync.por += If(por != 0, por.eq(por - 1))
        self.comb += self.cd_por.clk.eq(self.cd_sys.clk)
        self.specials += AsyncResetSynchronizer(self.cd_por, reset)
        self.specials += AsyncResetSynchronizer(self.cd_sys, ~pll_lckd | (por > 0))
        self.specials += Instance("BUFG", i_I=pll[2], o_O=self.cd_sdram_half.clk)
        self.specials += Instance("BUFPLL", p_DIVIDE=4,
                                  i_PLLIN=pll[0], i_GCLK=self.cd_sys.clk,
                                  i_LOCKED=pll_lckd, o_IOCLK=self.cd_sdram_full_wr.clk,
                                  o_SERDESSTROBE=self.clk4x_wr_strb)
        self.comb += [
            self.cd_sdram_full_rd.clk.eq(self.cd_sdram_full_wr.clk),
            self.clk4x_rd_strb.eq(self.clk4x_wr_strb),
        ]
        clk_sdram_half_shifted = Signal()
        self.specials += Instance("BUFG", i_I=pll[3], o_O=clk_sdram_half_shifted)

        output_clk = Signal()
        clk = platform.request("ddram_clock")
        self.specials += Instance("ODDR2", p_DDR_ALIGNMENT="NONE",
                                  p_INIT=0, p_SRTYPE="SYNC",
                                  i_D0=1, i_D1=0, i_S=0, i_R=0, i_CE=1,
                                  i_C0=clk_sdram_half_shifted, i_C1=~clk_sdram_half_shifted,
                                  o_Q=output_clk)
        self.specials += Instance("OBUFDS", i_I=output_clk, o_O=clk.p, o_OB=clk.n)

        dcm_base50_locked = Signal()
        self.specials += Instance("DCM_CLKGEN",
                                  p_CLKFXDV_DIVIDE=2, p_CLKFX_DIVIDE=4, p_CLKFX_MD_MAX=1.0, p_CLKFX_MULTIPLY=2,
                                  p_CLKIN_PERIOD=10.0, p_SPREAD_SPECTRUM="NONE", p_STARTUP_WAIT="FALSE",

                                  i_CLKIN=clk100a, o_CLKFX=self.cd_base50.clk,
                                  o_LOCKED=dcm_base50_locked,
                                  i_FREEZEDCM=0, i_RST=ResetSignal())
        self.specials += AsyncResetSynchronizer(self.cd_base50, self.cd_sys.rst | ~dcm_base50_locked)
        platform.add_period_constraint(self.cd_base50.clk, 20)


class BaseSoC(SDRAMSoC):
    default_platform = "opsis"

    csr_map = {
        "ddrphy":   16,
    }
    csr_map.update(SDRAMSoC.csr_map)

    mem_map = {
        "firmware_ram": 0x20000000,  # (default shadow @0xa0000000)
    }
    mem_map.update(SDRAMSoC.mem_map)

    def __init__(self, platform, firmware_ram_size=0x8000, **kwargs):
        clk_freq = 75*1000000
        SDRAMSoC.__init__(self, platform, clk_freq,
#                          uart_baudrate=9600,
                          integrated_rom_size=0x8000,
                          sdram_controller_settings=LASMIconSettings(l2_size=128),
                          **kwargs)

        self.submodules.crg = _CRG(platform, clk_freq)

        self.submodules.firmware_ram = wishbone.SRAM(firmware_ram_size)
        self.register_mem("firmware_ram", self.mem_map["firmware_ram"], self.firmware_ram.bus, firmware_ram_size)

        if not self.integrated_main_ram_size:
            self.submodules.ddrphy = s6ddrphy.S6DDRPHY(platform.request("ddram"),
                                                       MT41J128M16(self.clk_freq),
                                                       rd_bitslip=1,
                                                       wr_bitslip=3,
                                                       dqs_ddr_alignment="C1")
            self.comb += [
                self.ddrphy.clk4x_wr_strb.eq(self.crg.clk4x_wr_strb),
                self.ddrphy.clk4x_rd_strb.eq(self.crg.clk4x_rd_strb),
            ]
            self.register_sdram_phy(self.ddrphy)

        self.specials += Keep(self.crg.cd_sys.clk)
        platform.add_platform_command("""
NET "{sys_clk}" TNM_NET = "GRPsys_clk";
""", sys_clk=self.crg.cd_sys.clk)

        self.comb += [platform.request("debug").eq(platform.lookup_request("serial").tx)]


class MiniSoC(BaseSoC):
    csr_map = {
        "ethphy": 17,
        "ethmac": 18,
    }
    csr_map.update(BaseSoC.csr_map)

    interrupt_map = {
        "ethmac": 2,
    }
    interrupt_map.update(BaseSoC.interrupt_map)

    mem_map = {
        "ethmac": 0x30000000,  # (shadow @0xb0000000)
    }
    mem_map.update(BaseSoC.mem_map)

    def __init__(self, platform, **kwargs):
        BaseSoC.__init__(self, platform, **kwargs)

        self.submodules.ethphy = LiteEthPHYMII(platform.request("eth_clocks"), platform.request("eth"))
        self.submodules.ethmac = LiteEthMAC(phy=self.ethphy, dw=32, interface="wishbone")
        self.add_wb_slave(mem_decoder(self.mem_map["ethmac"]), self.ethmac.bus)
        self.add_memory_region("ethmac", self.mem_map["ethmac"]+self.shadow_base, 0x2000)

        self.specials += [
            Keep(self.ethphy.crg.cd_eth_rx.clk),
            Keep(self.ethphy.crg.cd_eth_tx.clk)
        ]
        platform.add_platform_command("""
NET "{eth_clocks_rx}" CLOCK_DEDICATED_ROUTE = FALSE;
NET "{eth_rx_clk}" TNM_NET = "GRPeth_rx_clk";
NET "{eth_tx_clk}" TNM_NET = "GRPeth_tx_clk";
TIMESPEC "TSise_sucks1" = FROM "GRPeth_tx_clk" TO "GRPsys_clk" TIG;
TIMESPEC "TSise_sucks2" = FROM "GRPsys_clk" TO "GRPeth_tx_clk" TIG;
TIMESPEC "TSise_sucks3" = FROM "GRPeth_rx_clk" TO "GRPsys_clk" TIG;
TIMESPEC "TSise_sucks4" = FROM "GRPsys_clk" TO "GRPeth_rx_clk" TIG;
""", eth_clocks_rx=platform.lookup_request("eth_clocks").rx,
     eth_rx_clk=self.ethphy.crg.cd_eth_rx.clk,
     eth_tx_clk=self.ethphy.crg.cd_eth_tx.clk)


default_subtarget = BaseSoC
