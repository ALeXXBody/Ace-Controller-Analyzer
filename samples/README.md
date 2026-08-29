# Board data samples

Each `.json` file here is a diagnostic export from a real CD3217/ACE2 board,
named after the MacBook/board model (e.g. `A2141.json`). A file contains one
board's exported data: device INFO frame, register dump, OTP scan, SPI flash
ROM, UART capture, and the diagnostic report, plus a UTC timestamp and the app
version that produced it.

The app's **Export data** button pushes files here automatically (with a
token); you can also drop files via the web UI ("Add file -> Upload files").

Files are studied to understand these chips and improve the diagnostic tool.
