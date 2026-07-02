#include <Wire.h>
#include <Adafruit_PWMServoDriver.h>

Adafruit_PWMServoDriver pwm = Adafruit_PWMServoDriver();

// Servo
#define SERVOMIN   150
#define SERVOMAX   600
#define SERVO_FREQ  60
#define HEAD        0
#define WAIST       1

// Relay
#define RELAY_PIN   5

// Stepper
#define EN_PIN      8
#define STEP_PIN    9
#define DIR_PIN     10
#define STEP_DELAY  100

bool          moving    = false;
unsigned long moveStart = 0;
unsigned long moveDur   = 0;

// ─────────────────────────────────────────────────────────────
void set_servo(uint8_t port, uint8_t angle) {
  angle = constrain(angle, 0, 180);
  pwm.setPWM(port, 0, map(angle, 0, 180, SERVOMIN, SERVOMAX));
}

void do_ready()    { set_servo(WAIST, 90);  set_servo(HEAD, 90);  Serial.println("OK:READY"); }
void do_confused() {
  set_servo(WAIST, 130); delay(300);
  set_servo(HEAD,   45); delay(1000);
  set_servo(HEAD,   90); delay(200);
  set_servo(WAIST,  90); Serial.println("OK:CONFUSED");
}
void do_shake() {
  set_servo(WAIST, 90);
  set_servo(HEAD,  45); delay(300);
  set_servo(HEAD, 135); delay(300);
  set_servo(HEAD,  45); delay(300);
  set_servo(HEAD, 135); delay(300);
  set_servo(HEAD,  90); Serial.println("OK:SHAKE");
}
void do_nod() {
  set_servo(HEAD,  60); delay(300);
  set_servo(HEAD, 110); delay(300);
  set_servo(HEAD,  60); delay(300);
  set_servo(HEAD,  90); Serial.println("OK:NOD");
}
void do_dance() {
  for (int i = 0; i < 3; i++) {
    set_servo(WAIST,  40); set_servo(HEAD,  30); delay(400);
    set_servo(WAIST, 140); set_servo(HEAD, 150); delay(400);
  }
  do_ready(); Serial.println("OK:DANCE");
}
void do_sleep() {
  set_servo(HEAD, 130); set_servo(WAIST, 70);
  Serial.println("OK:SLEEP");
}

// ─────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  Serial.setTimeout(10);

  pinMode(RELAY_PIN, OUTPUT); digitalWrite(RELAY_PIN, HIGH);
  pinMode(EN_PIN,    OUTPUT); digitalWrite(EN_PIN,    LOW);
  pinMode(STEP_PIN,  OUTPUT);
  pinMode(DIR_PIN,   OUTPUT); digitalWrite(DIR_PIN,   LOW);

  pwm.begin();
  pwm.setOscillatorFrequency(27000000);
  pwm.setPWMFreq(SERVO_FREQ);
  delay(10);

  do_ready();
  Serial.println("=== DRAVIXA Ready ===");
}

// ─────────────────────────────────────────────────────────────
void loop() {
  // Read command
  if (Serial.available() > 0) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd.length() == 0) goto step_check;
    cmd.toUpperCase();

    // AC / Fan
    if      (cmd == "ON")       { digitalWrite(RELAY_PIN, LOW);  Serial.println("OK:FAN_ON");  }
    else if (cmd == "OFF")      { digitalWrite(RELAY_PIN, HIGH); Serial.println("OK:FAN_OFF"); }

    // Window / Stepper
    else if (cmd == "F") { digitalWrite(DIR_PIN, LOW);  moving=true; moveStart=millis(); moveDur=7500; Serial.println("OK:WIN_CLOSE"); }
    else if (cmd == "B") { digitalWrite(DIR_PIN, HIGH); moving=true; moveStart=millis(); moveDur=7500; Serial.println("OK:WIN_OPEN");  }
    else if (cmd == "H") { digitalWrite(DIR_PIN, HIGH); moving=true; moveStart=millis(); moveDur=3750; Serial.println("OK:WIN_HALF");  }
    else if (cmd == "S") { moving=false; moveDur=0; Serial.println("OK:WIN_STOP"); }

    // Bot emotes
    else if (cmd == "READY")    { do_ready();    }
    else if (cmd == "CONFUSED") { do_confused(); }
    else if (cmd == "SHAKE")    { do_shake();    }
    else if (cmd == "NOD")      { do_nod();      }
    else if (cmd == "DANCE")    { do_dance();    }
    else if (cmd == "SLEEP")    { do_sleep();    }

    // Manual: port,angle
    else if (cmd.indexOf(',') > 0) {
      int ci = cmd.indexOf(',');
      int p  = cmd.substring(0, ci).toInt();
      int a  = cmd.substring(ci + 1).toInt();
      if (p == HEAD || p == WAIST) {
        set_servo(p, a);
        Serial.println("OK:MANUAL");
      } else {
        Serial.println("ERR:PORT");
      }
    }
    else { Serial.println("ERR:UNKNOWN"); }
  }

  // Auto-stop stepper
  step_check:
  if (moving && moveDur > 0 && millis() - moveStart >= moveDur) {
    moving = false; moveDur = 0;
    Serial.println("OK:WIN_STOPPED");
  }

  // Step if moving
  if (moving) {
    digitalWrite(STEP_PIN, HIGH); delayMicroseconds(STEP_DELAY);
    digitalWrite(STEP_PIN, LOW);  delayMicroseconds(STEP_DELAY);
  }
}
