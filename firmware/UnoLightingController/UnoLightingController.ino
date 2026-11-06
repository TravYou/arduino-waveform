// Uno R3 controller for one 1 m, 12 V WS2811 COB strip (20 addressable sections).
// D6 -> strip DI, with a shared ground. The strip needs its own 12 V supply.
// Install Adafruit NeoPixel in Arduino IDE. Serial protocol: 115200 baud, LF.

#include <Adafruit_NeoPixel.h>
#include <string.h>

const uint8_t DATA_PIN = 6;
const uint8_t SECTION_COUNT = 20;
const uint8_t MAX_STEPS = 32;
const unsigned long MAX_DURATION_MS = 86400000UL;
const unsigned long MIN_DURATION_MS = 1000UL;

// This strip showed green when sent red with NEO_GRB, so use RGB byte order.
// Verify the data rate on the physical strip before color-critical sequences.
Adafruit_NeoPixel strip(SECTION_COUNT, DATA_PIN, NEO_RGB + NEO_KHZ800);

enum StepMode { SOLID, BLINK, DARK };
enum State { IDLE, LOADING, READY, RUNNING, MANUAL };

struct Step {
  unsigned long durationMs;
  unsigned int periodMs;
  uint8_t red;
  uint8_t green;
  uint8_t blue;
  uint8_t brightness;
  uint8_t mode;
};

Step steps[MAX_STEPS];
State state = IDLE;
uint8_t expectedCount = 0;
uint8_t loadedCount = 0;
uint8_t committedCount = 0;
uint8_t activeIndex = 0;
unsigned long stepStartedAt = 0;

char line[96];
uint8_t lineLength = 0;
bool droppingLongLine = false;
bool shownValid = false;
uint8_t shownRed = 0;
uint8_t shownGreen = 0;
uint8_t shownBlue = 0;

void renderColor(uint8_t red, uint8_t green, uint8_t blue) {
  if (shownValid && red == shownRed && green == shownGreen && blue == shownBlue) return;
  shownValid = true;
  shownRed = red;
  shownGreen = green;
  shownBlue = blue;
  strip.fill(strip.Color(red, green, blue));
  strip.show();
}

void renderOff() { renderColor(0, 0, 0); }

uint8_t scaled(uint8_t component, uint8_t brightness) {
  return (uint16_t(component) * brightness + 127U) / 255U;
}

void renderStep(const Step &step, unsigned long elapsedMs) {
  if (step.mode == DARK ||
      (step.mode == BLINK && elapsedMs % step.periodMs >= step.periodMs / 2U)) {
    renderOff();
    return;
  }
  renderColor(scaled(step.red, step.brightness),
              scaled(step.green, step.brightness),
              scaled(step.blue, step.brightness));
}

void updatePlayback(unsigned long now) {
  if (state != RUNNING) return;
  // Advance from scheduled boundaries so serial traffic cannot accumulate drift.
  while (state == RUNNING && now - stepStartedAt >= steps[activeIndex].durationMs) {
    stepStartedAt += steps[activeIndex].durationMs;
    ++activeIndex;
    if (activeIndex >= committedCount) {
      state = READY;
      renderOff();
      return;
    }
  }
  renderStep(steps[activeIndex], now - stepStartedAt);
}

bool parseNumber(const char *token, unsigned long maximum, unsigned long &result) {
  if (!token || !*token) return false;
  unsigned long value = 0;
  for (const char *p = token; *p; ++p) {
    if (*p < '0' || *p > '9') return false;
    uint8_t digit = *p - '0';
    if (value > (maximum - digit) / 10UL) return false;
    value = value * 10UL + digit;
  }
  result = value;
  return true;
}

uint8_t split(char *input, char *tokens[], uint8_t capacity) {
  uint8_t count = 0;
  for (char *token = strtok(input, " \t"); token; token = strtok(NULL, " \t")) {
    if (count == capacity) return capacity + 1;
    tokens[count++] = token;
  }
  return count;
}

void error(const __FlashStringHelper *reason) {
  Serial.print(F("ERR "));
  Serial.println(reason);
}

void printStatus() {
  updatePlayback(millis());
  Serial.print(F("OK STATUS "));
  switch (state) {
    case IDLE: Serial.print(F("IDLE")); break;
    case LOADING: Serial.print(F("LOADING")); break;
    case READY: Serial.print(F("READY")); break;
    case RUNNING: Serial.print(F("RUNNING")); break;
    case MANUAL: Serial.print(F("MANUAL")); break;
  }
  Serial.print(' ');
  if (state == RUNNING) Serial.print(activeIndex);
  else Serial.print(F("-1"));
  Serial.print(' ');
  if (state == RUNNING) Serial.print(millis() - stepStartedAt);
  else Serial.print('0');
  Serial.print(' ');
  if (state == LOADING) Serial.println(loadedCount);
  else Serial.println(committedCount);
}

void handleCommand(char *input) {
  char *args[10];
  uint8_t count = split(input, args, 10);
  if (count == 0 || count > 10) { error(F("SYNTAX")); return; }

  if (strcmp(args[0], "HELLO") == 0 && count == 1) {
    Serial.println(F("OK HELLO 1 32"));
    return;
  }
  if (strcmp(args[0], "STATUS") == 0 && count == 1) {
    printStatus();
    return;
  }
  if (strcmp(args[0], "STOP") == 0 && count == 1) {
    state = committedCount ? READY : IDLE;
    renderOff();
    Serial.println(F("OK STOP"));
    return;
  }
  if (strcmp(args[0], "OFF") == 0 && count == 1) {
    state = committedCount ? READY : IDLE;
    renderOff();
    Serial.println(F("OK OFF"));
    return;
  }
  if (strcmp(args[0], "SET") == 0 && count == 5) {
    unsigned long values[4];
    for (uint8_t i = 0; i < 4; ++i) {
      if (!parseNumber(args[i + 1], 255UL, values[i])) {
        error(F("VALUE")); return;
      }
    }
    state = MANUAL;
    renderColor(scaled(values[0], values[3]),
                scaled(values[1], values[3]), scaled(values[2], values[3]));
    Serial.println(F("OK SET"));
    return;
  }
  if (strcmp(args[0], "BEGIN") == 0 && count == 2) {
    unsigned long requested;
    if (!parseNumber(args[1], MAX_STEPS, requested) || requested < 1) {
      error(F("COUNT")); return;
    }
    state = LOADING;
    expectedCount = requested;
    loadedCount = 0;
    committedCount = 0;
    renderOff();
    Serial.println(F("OK BEGIN"));
    return;
  }
  if (strcmp(args[0], "STEP") == 0 && count == 9) {
    if (state != LOADING) { error(F("STATE")); return; }
    unsigned long index, red, green, blue, brightness, duration, period;
    if (!parseNumber(args[1], MAX_STEPS - 1, index) || index != loadedCount ||
        loadedCount >= expectedCount) { error(F("INDEX")); return; }
    if (strlen(args[2]) != 1 ||
        (args[2][0] != 'S' && args[2][0] != 'B' && args[2][0] != 'O')) {
      error(F("MODE")); return;
    }
    if (!parseNumber(args[3], 255UL, red) ||
        !parseNumber(args[4], 255UL, green) ||
        !parseNumber(args[5], 255UL, blue) ||
        !parseNumber(args[6], 255UL, brightness) ||
        !parseNumber(args[7], MAX_DURATION_MS, duration) ||
        !parseNumber(args[8], 10000UL, period) ||
        duration < MIN_DURATION_MS || duration % 1000UL != 0 ||
        (args[2][0] == 'B' ? period < 100UL : period != 0UL)) {
      error(F("VALUE")); return;
    }
    Step &step = steps[loadedCount];
    step.durationMs = duration;
    step.periodMs = period;
    step.red = red;
    step.green = green;
    step.blue = blue;
    step.brightness = brightness;
    step.mode = args[2][0] == 'S' ? SOLID : (args[2][0] == 'B' ? BLINK : DARK);
    ++loadedCount;
    Serial.println(F("OK STEP"));
    return;
  }
  if (strcmp(args[0], "COMMIT") == 0 && count == 1) {
    if (state != LOADING || loadedCount != expectedCount) {
      error(F("INCOMPLETE")); return;
    }
    committedCount = loadedCount;
    state = READY;
    Serial.println(F("OK COMMIT"));
    return;
  }
  if (strcmp(args[0], "START") == 0 && count == 1) {
    if (!committedCount || state == LOADING || state == RUNNING) {
      error(F("STATE")); return;
    }
    activeIndex = 0;
    stepStartedAt = millis();
    state = RUNNING;
    updatePlayback(stepStartedAt);
    Serial.println(F("OK START"));
    return;
  }
  error(F("SYNTAX"));
}

void readSerialLines() {
  while (Serial.available() > 0) {
    char received = (char)Serial.read();
    if (received == '\r') continue;
    if (received == '\n') {
      if (droppingLongLine) error(F("LINE_TOO_LONG"));
      else {
        line[lineLength] = '\0';
        handleCommand(line);
      }
      lineLength = 0;
      droppingLongLine = false;
    } else if (!droppingLongLine) {
      if (lineLength < sizeof(line) - 1) line[lineLength++] = received;
      else droppingLongLine = true;
    }
  }
}

void setup() {
  strip.begin();
  renderOff();
  Serial.begin(115200);
  Serial.println(F("READY 1 32"));
}

void loop() {
  readSerialLines();
  updatePlayback(millis());
}
