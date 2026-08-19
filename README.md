# Project Name

### Vision

TBD

### High-Level Goals

TBD

---

## Project Charter

Project Charter is availbe on [charters/api.md](https://github.com/natiq-foundation/charters/blob/main/charters/api.md).

---

## Installation & Running

### With Docker (recommended)

```bash
git clone https://github.com/natiq-foundation/quran-api.git
cd your-repo
cp .env.example .env
# edit .env with your settings
docker compose up -d --build
```

The service should now be available at http://localhost:8000.

### other methods

### Development

For faster local development without Docker:
* Create a new virtual environment
```bash
python3 -m venv .venv
source .venv/bin/activate
```

* Install required packages
```bash
pip3 install -r ./requirements.txt
```

* Fill out .env file
```bash
cp .env.example .env
vim .env
```

* Run migrations
```
python3 manage.py migrate
```

* Start server!
```
python3 manage.py runserver
```

### Testing

**WIP**
