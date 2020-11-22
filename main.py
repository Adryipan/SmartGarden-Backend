import atexit
import uuid

import RPi.GPIO as GPIO
import time
# Import the ADS1x15 module.
import Adafruit_ADS1x15
import board
import busio
# Import the lux sensor module
import adafruit_veml7700
# Firebase API
from firebase import firebase
# Flask
from flask import Flask
from flask import request
from flaskthreads import AppContextThread
import threading

# Firebase admin
import firebase_admin
from firebase_admin import credentials
from firebase_admin import db


# Get machine id
UUID = str(uuid.getnode())

# Save User id
uid = ''

# Thread for sensor system
thread = threading.Thread()

# Firebase API key
fireEndPoint = 'https://smart-garden-d6653.firebaseio.com'
cred = credentials.Certificate("smart-garden-d6653-firebase-adminsdk-6p8a4-a8b5120006.json")
firebase_admin.initialize_app(cred, {
    'databaseURL': fireEndPoint,

})
# firebase = None

# # For debugging
# firebase.delete()


# For setting up the ADC
adc = Adafruit_ADS1x15.ADS1115()
GAIN = 2 / 3
# AO Channel for Moisture is 0
# AO channel for Temperature is 1
temperatureAo = 1
moistureAo = 0
# Lux sensor setup
i2c = busio.I2C(board.SCL, board.SDA)
luxSensor = adafruit_veml7700.VEML7700(i2c)
luxA0 = 2

# Moisture sensor calibration test results without soil
# Range 20523 -> 0%, 17366 -> 100%. Assume 18935 -> 50%
moistureZero = 19000

# Water pump
motorPin = 7
# Flowrate of 8ml/s
flowRate = 14
total = 0
container = total

run = False
# Time gap between each probe in second
customMoisture = 60
probeTime = 4


def sensorServer_app():
    app = Flask(__name__)

    def steinhart_temperature_C(r, Ro=10000.0, To=25.0, beta=3950.0):
        import math
        steinhart = math.log(r / Ro) / beta  # log(R/Ro) / beta
        steinhart += 1.0 / (To + 273.15)  # log(R/Ro) / beta + 1/To
        steinhart = (1.0 / steinhart) - 273.15  # Invert, convert to C
        return steinhart

    def init_output(pin):
        GPIO.setup(pin, GPIO.OUT)
        GPIO.output(pin, GPIO.LOW)
        GPIO.output(pin, GPIO.HIGH)

    def pump_on(pump_pin=4):
        init_output(pump_pin)
        GPIO.output(pump_pin, GPIO.LOW)
        time.sleep(1)
        GPIO.output(pump_pin, GPIO.HIGH)

    @app.route('/pair', methods=['POST'])
    def getUUID():
        data = request.json
        user = data['uid']
        global uid
        uid = user

        return UUID


    @app.route('/water')
    def water():
        pump_on()
        time.sleep(1)
        GPIO.cleanup(4)
        return 'Watered'

    @app.route('/setMoisture/<newMoisture>')
    def setCustomMoisture(newMoisture):
        global customMoisture
        customMoisture = int(newMoisture)
        return 'Moisture set to ' + str(newMoisture) + '\n'

    @app.route('/setContainerVolumn/<newVolumn>')
    def setContainerVolumn(newVolumn):
        global total
        total = int(newVolumn)
        return 'Volumn set to ' + str(total) + '\n'

    @app.route('/start/<userid>')
    def start(userid):
        global uid
        uid = userid
        global run
        run = True
        global thread
        thread = threading.Thread(target=main, args=())
        thread.start()
        # Reference it on the firebase for checking
        ref = db.reference('/' + uid + '/' + UUID , None)
        ref.child('auto_water').set(True)

        return 'Started\n'

    @app.route('/stop')
    def stop():
        global run
        run = False
        # Reference it on the firebase for checking
        ref = db.reference('/' + uid + '/' + UUID , None)
        ref.child('auto_water').set(False)
        return 'Stopped\n'

    @app.route('/setProbeTime/<newProbeTime>')
    def setProbeTime(newProbeTime):
        global probeTime
        probeTime = int(newProbeTime)
        return 'New probe time set to ' + str(probeTime) + '\n'

    def main():
        print('| {0:>11} | {1:>11} | {2:>11} | {3:>11} | {4:>11} | {5:>11} | {6:>11}'
            .format(
            *['Moisture', 'Temperature', 'Lux', 'Watered', 'Moisture threshold', 'Probing Time', 'Water left']))
        global run
        global customMoisture
        global probeTime
        global container
        while run:
            record = [0] * 7
            # Represent if water is triggered
            record[3] = False
            # Current watering moisture threshold
            record[4] = customMoisture
            # Current Probing time
            record[5] = probeTime
            # Calculate moisture
            moisture = (100 - (adc.read_adc(moistureAo, gain=GAIN) / moistureZero) * 100) * 4
            if moisture > 100:
                moisture = 100
            # record[moistureAo] = adc.read_adc(moistureAo, gain=GAIN)
            record[moistureAo] = round(moisture, 2)
            # Check if the moisture is lower than the threshold. If yes, water
            if record[moistureAo] < customMoisture:
                pump_on()
                waterTime = 1
                time.sleep(waterTime)
                container -= flowRate * waterTime
                GPIO.cleanup(4)
                record[3] = True

            # Calculate the temperature in Celsius
            thermistorResistant = adc.read_adc(temperatureAo, gain=GAIN)
            record[temperatureAo] = round(steinhart_temperature_C(thermistorResistant), 2)

            # Record lux reading
            record[luxA0] = round(luxSensor.lux, 2)

            # Record the remaining water volumn
            record[6] = container

            # Print result
            print('| {0:>11} | {1:>11} | {2:>11} | {3:>11} | {4:>18} | {5:>12} | {6:>11}'.format(*record))

            firebase = db.reference('/' + uid + '/' + UUID + '/data', None)
            # Upload to Firebase
            firebase.push({

                'Timestamp': {".sv": "timestamp"},
                'Moisture': round(record[moistureAo],1),
                'Temperature': round(record[temperatureAo],1),
                'Lux': round(record[luxA0],1),
                'Watered': record[3],
                'Moisture threshold': record[4],
                'Probing period': record[5],
                'Water level': record[6],
                'Container Volume': total

            })

            # Set gap between probe
            time.sleep(probeTime)
        GPIO.cleanup(4)

    # Called when Flask is interrupted. If called, stop the thread and
    def interrupt():
        global run
        run = False

    atexit.register(interrupt)

    return app


if __name__ == '__main__':
    app = sensorServer_app()
    app.run(debug=True, host='0.0.0.0', port=5000)
