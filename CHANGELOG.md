# Changelog

## 0.2.0

- Wait one second after connecting before sending TV commands.
- Use state-aware `POWER` retries when the current state is known.
- Keep `WAKEUP` and `SLEEP` as non-toggle fallbacks when state is unknown.
- Add terminal-first power, HDMI input, raw-key, and USB wake tests.
- Test HDMI switching and an optional off/on cycle before setup enables automation.
- Trace each detected controller to its exact USB root hub and parent PCI wake state.

## 0.1.0

- Initial alpha implementation.
