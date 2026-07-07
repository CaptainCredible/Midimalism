/*
  midimalism2
  2 knobs + 2 buttons → MIDI CC or Note
  All parameters reprogrammable via SysEx, stored in EEPROM.

  SysEx — set CC/note number : F0 7D 01 <index 0-3> <number 0-127> F7
  SysEx — set channel        : F0 7D 02 <index 0-3> <channel 1-16> F7
  SysEx — set button mode    : F0 7D 03 <btn 0-1>   <mode 0=CC 1=Note> F7

    index 0 = knob 1   (analog pin 1)
    index 1 = knob 2   (analog pin 2)
    index 2 = button 1 (digital pin 0)
    index 3 = button 2 (digital pin 1)

  EEPROM layout:
    addr  0-3  : cc/note numbers  [0..127]
    addr  4-7  : channels         [1..16]
    addr  8-9  : button modes     [0=CC, 1=Note]
    addr  10   : magic (0xCC)
*/

/*
 * PROGRAM
  ~/Library/Arduino15/packages/arduino/tools/avrdude/6.3.0-arduino17/bin/avrdude \
  -C ~/Library/Arduino15/packages/arduino/tools/avrdude/6.3.0-arduino17/etc/avrdude.conf \
  -c usbasp -p attiny85 \
  -U flash:w:/Users/daniel/Desktop/combined.hex:i \
  -U lfuse:w:0xE1:m \
  -U hfuse:w:0x5D:m \
  -U efuse:w:0xFE:m
 * 
 */

#include "midimalism.h"
#include <EEPROM.h> 

// --- hardware pins ---
#define PIN_KNOB1  1
#define PIN_KNOB2  2
#define PIN_BUTT1  0
#define PIN_BUTT2  1

// --- noise filter ---
#define NOISE_RATIO  4   // 10-bit space (out of 1023) — ~0.4% of range

// --- debounce ---
#define DEBOUNCE_MS  10


// --- EEPROM ---
#define NUM_CONTROLS       4
#define EEPROM_NUM_BASE    0   // addr 0-3: cc/note numbers
#define EEPROM_CHAN_BASE    4   // addr 4-7: channels
#define EEPROM_MODE_BASE   8   // addr 8-9: button modes
#define EEPROM_MAGIC_ADDR  10
#define EEPROM_MAGIC_VAL   0xCC

// --- SysEx commands ---
#define SYSEX_MFR_ID        0x7D
#define SYSEX_CMD_SET_NUM   0x01
#define SYSEX_CMD_SET_CHAN  0x02
#define SYSEX_CMD_SET_MODE  0x03

// --- runtime state ---
uint8_t cc_numbers[NUM_CONTROLS] = {60, 61, 81, 82};  // cc or note number
uint8_t channels[NUM_CONTROLS]   = {1, 1, 1, 1};      // midi channel 1-16
uint8_t btn_modes[2]             = {0, 0};             // 0=CC, 1=Note

uint16_t knob_raw_last[2] = {0xFFFF, 0xFFFF};  // 0xFFFF = force first send
uint8_t  knob_value[2]    = {255, 255};

bool     butt_raw[2]         = {false, false};  // raw pin state
bool     butt_stable[2]      = {false, false};  // debounced state
bool     butt_sent[2]        = {false, false};  // last sent state
uint32_t butt_last_change[2] = {0, 0};          // timestamp of last raw change

uint8_t  sysex_cmd = 0;

// --- EEPROM ---
void eepromLoad() {
  if (EEPROM.read(EEPROM_MAGIC_ADDR) != EEPROM_MAGIC_VAL) return;
  for (uint8_t i = 0; i < NUM_CONTROLS; i++) {
    uint8_t n = EEPROM.read(EEPROM_NUM_BASE + i);
    if (n <= 127) cc_numbers[i] = n;
    uint8_t c = EEPROM.read(EEPROM_CHAN_BASE + i);
    if (c >= 1 && c <= 16) channels[i] = c;
  }
  for (uint8_t i = 0; i < 2; i++) {
    uint8_t m = EEPROM.read(EEPROM_MODE_BASE + i);
    if (m <= 1) btn_modes[i] = m;
  }
}

void eepromSaveNum(uint8_t index, uint8_t num) {
  EEPROM.write(EEPROM_NUM_BASE + index, num);
  EEPROM.write(EEPROM_MAGIC_ADDR, EEPROM_MAGIC_VAL);
}

void eepromSaveChan(uint8_t index, uint8_t ch) {
  EEPROM.write(EEPROM_CHAN_BASE + index, ch);
  EEPROM.write(EEPROM_MAGIC_ADDR, EEPROM_MAGIC_VAL);
}

void eepromSaveMode(uint8_t btn, uint8_t mode) {
  EEPROM.write(EEPROM_MODE_BASE + btn, mode);
  EEPROM.write(EEPROM_MAGIC_ADDR, EEPROM_MAGIC_VAL);
}

// --- SysEx receive ---
void processMidi() {
  MIDIMessage msg;
  while (TeenyMidi.read(&msg)) {
    if (msg.command == 0xF0 && msg.key == SYSEX_MFR_ID) {
      sysex_cmd = msg.value;
    } else if (sysex_cmd != 0) {
      if (sysex_cmd == SYSEX_CMD_SET_NUM) {
        // [index, num, F7]
        if (msg.value == 0xF7 && msg.command < NUM_CONTROLS && msg.key <= 127) {
          cc_numbers[msg.command] = msg.key;
          eepromSaveNum(msg.command, msg.key);
          // if a knob's number changed, force resend on next read
          if (msg.command < 2) knob_value[msg.command] = 255;
        }
      } else if (sysex_cmd == SYSEX_CMD_SET_CHAN) {
        // [index, channel, F7]
        if (msg.value == 0xF7 && msg.command < NUM_CONTROLS && msg.key >= 1 && msg.key <= 16) {
          channels[msg.command] = msg.key;
          eepromSaveChan(msg.command, msg.key);
          if (msg.command < 2) knob_value[msg.command] = 255;
        }
      } else if (sysex_cmd == SYSEX_CMD_SET_MODE) {
        // [btn_index, mode, F7]
        if (msg.value == 0xF7 && msg.command < 2 && msg.key <= 1) {
          btn_modes[msg.command] = msg.key;
          eepromSaveMode(msg.command, msg.key);
        }
      }
      sysex_cmd = 0;
    }
  }
}

// --- setup ---
void setup() {
  TeenyMidi.init();
  eepromLoad();
  pinMode(PIN_BUTT1, INPUT_PULLUP);
  pinMode(PIN_BUTT2, INPUT_PULLUP);
}

// --- loop ---
void loop() {
  processMidi();

// knob 1
uint8_t cc0 = MIDI_CONTROLCHANGE | ((channels[0] - 1) & 0x0F);

uint16_t raw0 = analogRead(PIN_KNOB1);
if (abs((int)raw0 - (int)knob_raw_last[0]) > NOISE_RATIO || knob_raw_last[0] == 0xFFFF) {
    knob_raw_last[0] = raw0;
    uint8_t val = raw0 >> 3;
    if (val != knob_value[0]) {
        TeenyMidi.send(cc0, cc_numbers[0], val);
        knob_value[0] = val;
    }
}

  // knob 2
  uint8_t cc1 = MIDI_CONTROLCHANGE | ((channels[1] - 1) & 0x0F);
  uint16_t raw1 = analogRead(PIN_KNOB2);
  if (abs((int)raw1 - (int)knob_raw_last[1]) > NOISE_RATIO || knob_raw_last[1] == 0xFFFF) {
    knob_raw_last[1] = raw1;
    uint8_t val1 = raw1 >> 3;
    if (val1 != knob_value[1]) {
      TeenyMidi.send(cc1, cc_numbers[1], val1);
      knob_value[1] = val1;
    }
  }

  // buttons — read raw, debounce, send on stable change
  for (uint8_t i = 0; i < 2; i++) {
    bool raw = !digitalRead(i == 0 ? PIN_BUTT1 : PIN_BUTT2);

    // detected a raw change — restart the timer
    if (raw != butt_raw[i]) {
      butt_raw[i] = raw;
      butt_last_change[i] = millis();
    }

    // raw has been stable for DEBOUNCE_MS — promote to stable
    if (butt_raw[i] != butt_stable[i] && (millis() - butt_last_change[i]) >= DEBOUNCE_MS) {
      butt_stable[i] = butt_raw[i];
    }

    // stable state changed since last send — send MIDI
    if (butt_stable[i] != butt_sent[i]) {
      uint8_t ctrl = i + 2;  // control index 2 or 3
      if (btn_modes[i] == 0) {
        uint8_t st = MIDI_CONTROLCHANGE | ((channels[ctrl] - 1) & 0x0F);
        TeenyMidi.send(st, cc_numbers[ctrl], butt_stable[i] ? 127 : 0);
      } else {
        uint8_t st = (butt_stable[i] ? MIDI_NOTEON : MIDI_NOTEOFF) | ((channels[ctrl] - 1) & 0x0F);
        TeenyMidi.send(st, cc_numbers[ctrl], butt_stable[i] ? 127 : 0);
      }
      butt_sent[i] = butt_stable[i];
    }
  }

  TeenyMidi.update();
}
