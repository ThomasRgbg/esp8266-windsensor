from machine import Pin, I2C, reset, RTC, Timer, ADC, WDT, unique_id
import time
import ntptime

import uasyncio
import gc
import micropython

from mqtt_handler import MQTTHandler
from relay import Relay

#####
# Schematic/Notes
######

#
# Wind sensor pulses
# to:   GPIO 2 

# Relay down GPIO0
# Relay up GPIO3  (Also UART RX)


time.sleep(5)
errcount = 0

def get_errcount():
    global errcount
    return errcount


#####
# Watchdog - 180 seconds, need to be larger then loop time below
#####

        
class Watchdog:
    def __init__(self, interval):
        self.timer = Timer(0)
        self.timer.init(period=(interval*1000), mode=Timer.PERIODIC, callback=self.wdtcheck)
        self.feeded = True
        
    def wdtcheck(self, timer):
        if self.feeded:
            print("Watchdog feeded, all fine")
            self.feeded = False
        else:
            print("Watchdog hungry, lets do a reset in 5 sec")
            time.sleep(5)
            reset()
            
    def feed(self):
        self.feeded = True
        print("Feed Watchdog")

wdt = Watchdog(interval = 120)

#####
# Relay outputs
#####

relay_up = Relay(3, invert=False)
relay_down = Relay(0, invert=False)

#####
# Wind Sensor
#####

class Wind:
    def __init__(self):
        self.gpio = Pin(2, mode=Pin.IN, pull=Pin.PULL_UP)
        self.ticks = 0
        self.speedfactor = 1.8     # 1 tick per second = 2.4 km/h (maybe a bit lower)
                                   # 20ms between tick = 120km/h
        self.debounce = 10         # Minmal time between two ticks (debouncer)         
        self.mindelta = 60*1000    # Init for finding minimal delta. 
        self.lastirq = 0           # Timestamp of last IRQ
        self.lastdelta = self.debounce
        self.windticks = []        # List to save deltas in IRQ

        self.speed = 0             # To save speed
        self.peakspeed = 0

        self.last_analyis = time.ticks_ms()

    def gpio_irq_callback(self, pin):
        #if (self.gpio.value() == 1):
        #delta = time.ticks_diff(time.ticks_ms(), self.lastirq)
        #self.lastirq = time.ticks_ms()
        #self.windticks.append(delta)
        self.gpio.value()
        self.windticks.append(time.ticks_ms())

    def analyser(self):

        analyser_delta = time.ticks_diff(time.ticks_ms(), self.last_analyis) / 1000
        self.last_analyis = time.ticks_ms()
#        print('Wind analyser, delta = {0}'.format(analyser_delta))

        # remove first element, since it is corrupted
        # timer runs as IRQ and blocks the GPIO irq
        # todo: use async 
#        if len(self.windticks > 2):
#            self.windticks.pop(0)

        for i in range(len(self.windticks)-1):

            delta = self.windticks[i+1] - self.windticks[i]
#            print(delta, end='')

            #if (delta > self.debounce) and (delta > (self.lastdelta/2)):
            if (delta > self.debounce):
                self.ticks += 1
#                print('w', end='')

                if delta < self.mindelta and (delta > (self.lastdelta/1.8)):
                    #if i > 1:
                    #    self.mindelta = self.windticks[i+1] - self.windticks[i-1]
                    #else:
                    self.mindelta = delta
#                    print('m', end='')

                self.lastdelta = delta

            else:
                pass
                #print("wind bounce")
#                print('x', end='')


#            print(' ')

        self.windticks = []

        self.speed = self.ticks * (self.speedfactor/analyser_delta) 
        self.ticks = 0
        self.peakspeed = 1000/self.mindelta * self.speedfactor
        # print('peak speed', self.peakspeed)
        self.mindelta = analyser_delta * 1000
        self.lastdelta = self.debounce
        # print('wind speed', self.speed)
 #       print(' ')

    def enable(self):
        self.gpio.irq(handler=self.gpio_irq_callback, trigger=Pin.IRQ_FALLING)
        
    def disable(self):
        pass
   
wind=Wind()

sc = MQTTHandler(b'pentling/windsensor', '192.168.0.13')
# sc.register_publisher('errcount', get_errcount)

sc.register_action('r_down_enable', relay_down.set_state)
sc.register_publisher('r_down', relay_down.get_state, False)

sc.register_action('r_up_enable', relay_up.set_state)
sc.register_publisher('r_up', relay_up.get_state, False)

#####
# Task definition
#####

async def housekeeping():
    global errcount
    count = 1

    while True:
        print("housekeeping()")
        print("Count: {0}".format(count))
        print("Error counter: {0}".format(errcount))
        
        wdt.feed()

        # Too many errors, e.g. could not connect to MQTT
        if errcount > 100:
            time.sleep(5)
            reset()

        if not wlan.isconnected():
            print("WLAN not connected")
            errcount += 25
            time.sleep(5)
            continue

        gc.collect()
        micropython.mem_info()

        count += 1
        await uasyncio.sleep_ms(60000)


async def handle_wind():
    global errcount
    wind.enable()
    while True:
        wind.analyser()
        if sc.isconnected():
            sc.publish_generic('wind',wind.speed)
            sc.publish_generic('windpeak',wind.peakspeed)

        await uasyncio.sleep_ms(20000)


async def handle_mqtt():
    global errcount

    while True:
        # Generic MQTT
        if sc.isconnected():
            print("handle_mqtt() - connected")
            for i in range(59):
                sc.mqtt.check_msg()
                await uasyncio.sleep_ms(1000)
            sc.publish_all()

        else:
            print("MQTT not connected - try to reconnect")
            sc.connect()
            errcount += 1
            await uasyncio.sleep_ms(19000)

        await uasyncio.sleep_ms(1000)

#####
# Main loop
#####

main_loop = uasyncio.get_event_loop()

main_loop.create_task(housekeeping())
main_loop.create_task(handle_wind())
main_loop.create_task(handle_mqtt())

main_loop.run_forever()
main_loop.close()

