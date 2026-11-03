"""SkyStar STM32F407 (only) pin map for the upper board. Source of truth for docs, header and schematic specs.

S-number = socket position: S1-S40 = SkyStar P1 pin n, S41-S80 = P2 pin n (LCKFB schematic "双路80PIN排针").
"""
import json, collections

P1 = ['PA2', 'VREF', 'PA0', 'PA1', 'PA4', 'PA3', 'PA6', 'PA5', 'PC4', 'PA7', 'PB0', 'PC5', 'PE7', 'PB1', 'PE9', 'PE8', 'PE11', 'PE10', 'PE13', 'PE12',
      'PE15', 'PE14', 'PB10', 'PB11', 'PB13', 'PB12', 'PB15', 'PB14', 'PD9', 'PD8', 'PD11', 'PD10', 'PD13', 'PD12', 'PD15', 'PD14', 'AGND', '3V3', 'GND', '5V']
P2 = ['PD3', 'PC9', 'PC8', 'PC12', 'PD2', 'PC11', 'PC10', 'PC13', 'PE6', 'PE5', 'PE4', 'PE3', 'PE2', 'PC0', 'PC1', 'PC2', 'PC3', 'PE1', 'PE0', 'PB9',
      'PB8', 'PB7', 'PB6', 'PB5', 'PB4', 'BAT', 'PD7', 'PD6', 'PD5', 'PD4', 'PD1', 'PD0', 'PA15', 'PA8', 'PC6', 'PC7', 'GND', '3V3', 'GND', '5V']
SOCK = {i + 1: p for i, p in enumerate(P1)} | {i + 41: p for i, p in enumerate(P2)}

# net: (gpio, function/AF, group, note)
MAP = {
    # to the lower board through J603 (方案 C：J603.38 保持 NC，下板 J513.37 保留 IMON)
    'CAN_RX': ('PD0', 'CAN1_RX AF9', '下板', ''), 'CAN_TX': ('PD1', 'CAN1_TX AF9', '下板', ''),
    'I2C_SCL': ('PB8', 'I2C1_SCL AF4', '板内 I2C', 'OLED、JY901S、EEPROM、下板 INA228/TCA9555、Qwiic 1'),
    'I2C_SDA': ('PB9', 'I2C1_SDA AF4', '板内 I2C', ''),
    'IOX_INT': ('PE10', 'GPIO EXTI10', '下板', '下板 TCA9555 中断；PE10=TIM1_CH1N，互补输出保持关闭'),
    'MOT_EN': ('PE12', 'GPIO', '下板', '电机域开关；PE12=TIM1_CH3N，互补输出保持关闭'),
    'BATADC': ('PB0', 'ADC12_IN8', '下板', '电池分压'),
    'MOT_L1': ('PE9', 'TIM1_CH1 AF1', '下板', '4 路同一定时器，20 kHz'), 'MOT_L2': ('PE14', 'TIM1_CH4 AF1', '下板', ''),
    'MOT_R1': ('PE11', 'TIM1_CH2 AF1', '下板', ''), 'MOT_R2': ('PE13', 'TIM1_CH3 AF1', '下板', ''),
    'ENC_L_A': ('PB4', 'TIM3_CH1 AF2', '下板', '左编码器改 TIM3，16 位，注意溢出'), 'ENC_L_B': ('PB5', 'TIM3_CH2 AF2', '下板', ''),
    'ENC_R_A': ('PA1', 'TIM2_CH2 AF1', '下板', '右编码器改 TIM2，32 位；A/B 相对原方案对调'), 'ENC_R_B': ('PA15', 'TIM2_CH1 AF1', '下板', ''),
    'STP_L': ('PB14', 'TIM12_CH1 AF9', '下板', '两路步进各用一个定时器；C 方案对调左右'), 'STP_R': ('PE5', 'TIM9_CH1 AF3', '下板', ''),
    'USTEP_TX': ('PC10', 'UART4_TX AF8', '下板', '步进驱动器总线；C 方案改用 UART4'), 'USTEP_RX': ('PC11', 'UART4_RX AF8', '下板', 'C 方案改用 UART4'),
    'SRV1': ('PD12', 'TIM4_CH1 AF2', '下板', '4 路同一定时器，50 Hz'), 'SRV2': ('PD14', 'TIM4_CH3 AF2', '下板', 'C 方案与 SRV3 对调'),
    'SRV3': ('PD13', 'TIM4_CH2 AF2', '下板', 'C 方案与 SRV2 对调'), 'SRV4': ('PD15', 'TIM4_CH4 AF2', '下板', ''),
    # serial connectors (5V/TX/RX/GND)
    'U1_TX': ('PB6', 'USART1_TX AF7', '串口座', 'DAP 调试串口插 J619'), 'U1_RX': ('PB7', 'USART1_RX AF7', '串口座', '串口座 J619，RX 串阻 R637'),
    'U2_TX': ('PD5', 'USART2_TX AF7', '串口座', 'C 方案改接串口座 J621'), 'U2_RX': ('PD6', 'USART2_RX AF7', '串口座', '串口座 J621，RX 串阻 R636'),
    'U3_TX': ('PD8', 'USART3_TX AF7', '串口座', '串口座 J620'), 'U3_RX': ('PD9', 'USART3_RX AF7', '串口座', '串口座 J620，RX 串阻 R615'),
    'U5_TX': ('PC12', 'UART5_TX AF8', '串口座', '串口座 J608；与板载 TF 共脚，TF 不用'), 'U5_RX': ('PD2', 'UART5_RX AF8', '串口座', '串口座 J608，RX 串阻 R638；与板载 TF 共脚，TF 不用'),
    'U6_TX': ('PC6', 'USART6_TX AF8', '串口座', '串口座 J607'), 'U6_RX': ('PC7', 'USART6_RX AF8', '串口座', '串口座 J607，RX 串阻 R616'),
    # I2C connectors
    'I2C2_SCL': ('PB10', 'I2C2_SCL AF4', 'I2C 座', 'Qwiic J624'), 'I2C2_SDA': ('PB11', 'I2C2_SDA AF4', 'I2C 座', 'Qwiic J624'),
    'I2C3_SCL': ('PA8', 'I2C3_SCL AF4', 'I2C 座', 'Qwiic J616'), 'I2C3_SDA': ('PC9', 'I2C3_SDA AF4', 'I2C 座', 'Qwiic J616；与板载 TF 共脚，TF 不用'),
    # grey sensors
    'GRAY1': ('PC1', 'ADC123_IN11', '灰度', ''), 'GRAY2': ('PC0', 'ADC123_IN10', '灰度', ''),
    'GRAY3': ('PB1', 'ADC12_IN9', '灰度', ''), 'GRAY4': ('PC5', 'ADC12_IN15', '灰度', ''),
    'GRAY5': ('PA3', 'ADC123_IN3', '灰度', ''), 'GRAY6': ('PA2', 'ADC123_IN2', '灰度', ''),
    # E01 on SPI1, shared with the SkyStar flash (flash CS = PA4 kept high)
    'SPI_SCK': ('PA5', 'SPI1_SCK AF5', 'E01', '与板载 Flash 共总线'), 'SPI_MISO': ('PA6', 'SPI1_MISO AF5', 'E01', ''),
    'SPI_MOSI': ('PA7', 'SPI1_MOSI AF5', 'E01', ''),
    'E01_CS': ('PC8', 'GPIO', 'E01', '与板载 TF 共脚，TF 不用'), 'E01_CE': ('PD3', 'GPIO', 'E01', '与板载 TF 卡检测共脚，TF 不用'),
    'E01_IRQ': ('PE8', 'GPIO EXTI8', 'E01', ''),
    # HMI / misc
    'IMU_INT': ('PB13', 'GPIO EXTI13', 'IMU', '与 KEY5=PC13 共用 EXTI13 线，按键轮询'),
    'EC11_A': ('PB12', 'GPIO EXTI12', '交互', ''), 'EC11_B': ('PE0', 'GPIO EXTI0', '交互', ''),
    'EC11_P': ('PD10', 'GPIO', '交互', '旋钮按下；与 IOX_INT 共用 EXTI10 线，按键轮询'),
    'KEY1': ('PE4', 'GPIO 上拉输入', '交互', '五向键'), 'KEY2': ('PE3', 'GPIO 上拉输入', '交互', ''),
    'KEY3': ('PE2', 'GPIO 上拉输入', '交互', ''), 'KEY4': ('PE6', 'GPIO 上拉输入', '交互', ''),
    'KEY5': ('PC13', 'GPIO 上拉输入', '交互', '中键；PC13 只作输入，与 IMU_INT 共用 EXTI13 线'),
    'BEEP': ('PE1', 'GPIO', '交互', '经三极管驱动'),
    'RGB_R': ('PB15', 'GPIO', '交互', ''), 'RGB_G': ('PD4', 'GPIO', '交互', ''), 'RGB_B': ('PD7', 'GPIO', '交互', ''),
}
RESERVED = {'PA0': '天空星用户按键（10k 串阻 + 10k 下拉），不接', 'PA4': '天空星 SPI Flash 片选（10k 上拉），固件保持高电平'}

if __name__ == '__main__':
    gpio = collections.Counter(v[0] for v in MAP.values())
    rev = {p: s for s, p in SOCK.items()}
    exti = collections.Counter(int(v[0][2:]) for v in MAP.values() if 'EXTI' in v[1])
    used = set(gpio) | set(RESERVED)
    spare = [p for p in P1 + P2 if p.startswith('P') and p not in used]
    print('signals', len(MAP), '| duplicate gpio', [g for g, n in gpio.items() if n > 1],
          '| not on socket', [n for n, v in MAP.items() if v[0] not in rev], '| EXTI clash', [k for k, n in exti.items() if n > 1])
    print('spare', spare)
    json.dump({'sock': SOCK, 'map': MAP, 'reserved': RESERVED, 'spare': spare},
              open('pinmap_sky.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
