# Vedic Atlas

A structured repository for Vedic Atlas.

## Folder Structure

```
vedic-atlas/
├── .vscode/
│   └── settings.json
├── config/
├── scripts/
├── src/
│   ├── controller/          # CLI + REST routes
│   ├── core/
│   ├── domain/
│   ├── exceptions/
│   ├── schemas/
│   ├── service/
│   ├── tpa/
│   │   └── online/
│   │       └── allow_list.py
│   └── utilities/
├── .env.example
├── LICENSE
├── README.md
├── pyproject.toml
├── run.bat
└── run.sh
```

## Getting Started

### Prerequisites
- Python 3.10+

### Setup & Run
`run.bat` (Windows) and `./run.sh` (Linux/macOS) start the `veda` CLI. With no arguments
it opens the REPL and starts the local server (the voice loop runs inside it) if one is not
already running. `veda server` runs the server on its own.
