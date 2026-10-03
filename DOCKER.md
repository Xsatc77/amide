# Running Amide with Docker

This guide covers installing and running Amide using Docker Compose.

## Prerequisites

- **Docker** (version 20.10 or newer)
- **Docker Compose** (version 1.29 or newer)
- At least 1GB free disk space for the database and uploaded files

## Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/Xsatc77/amide.git
cd amide
```

### 2. Configure Environment

Copy the example environment file and adjust settings:

```bash
cp .env.example .env
```

Edit `.env` if you need to change the port or other settings:

```bash
# Change the host port (left side) if 1707 is already in use
AMIDE_PORT=1707

# IMPORTANT: Generate a new SECRET_KEY for production
# python -c "import secrets; print(secrets.token_urlsafe(32))"
SECRET_KEY=your-generated-key-here
```

### 3. Start Amide

```bash
docker compose up -d
```

The application will be available at `http://localhost:1707` (or the port you configured).

### 4. Initialize the Database

On first run, the database is automatically created with all required tables:

- **users** - User accounts and authentication
- **inventory_items** - Peptides, medicines, BAC water, and supplies
- **orders** - Purchase orders and shipment tracking
- **active_vials** - Reconstituted vials with expiration dates
- **protocols** - Dosing protocols and schedules
- **dose_logs** - Logged doses and adherence tracking
- **measurements** - Body metrics (weight, blood pressure, etc.)
- **workouts** - Workout tracking and logs
- **vendors** - Supplier information
- **sharing** - Multi-user sharing permissions

## Managing Amide

### View Logs

```bash
# Follow logs in real-time
docker compose logs -f amide

# View last 100 lines
docker compose logs amide | tail -100
```

### Stop Amide

```bash
docker compose down
```

### Restart Amide

```bash
docker compose restart amide
```

### Reset Everything (⚠️ Deletes all data)

```bash
docker compose down -v
docker compose up -d
```

## Data Backup

Your data is stored in the `./data` folder. Back it up regularly:

```bash
# Create a backup
tar -czf amide-backup-$(date +%Y%m%d).tar.gz ./data

# Restore from backup
tar -xzf amide-backup-20240101.tar.gz
```

## Using a Different Port

Edit `.env`:

```bash
AMIDE_PORT=8000  # Change to any available port
```

Then restart:

```bash
docker compose down
docker compose up -d
```

Access at `http://localhost:8000`

## Using PostgreSQL

For multi-user deployments, use PostgreSQL instead of SQLite:

1. Add PostgreSQL to `docker-compose.yml`:

```yaml
  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: amide
      POSTGRES_USER: amide
      POSTGRES_PASSWORD: secure-password
    volumes:
      - postgres_data:/var/lib/postgresql/data
    restart: unless-stopped

volumes:
  postgres_data:
```

2. Update `.env`:

```bash
DATABASE_URL=postgresql://amide:secure-password@postgres:5432/amide
```

3. Restart:

```bash
docker compose down
docker compose up -d
```

## Troubleshooting

### Port Already in Use

Change `AMIDE_PORT` in `.env` to an available port, then restart.

### Database Locked

If you see "database is locked" errors, restart the container:

```bash
docker compose restart amide
```

### High Disk Usage

The database grows with logged data. Monitor the `./data` folder:

```bash
du -sh ./data
```

### Application Won't Start

Check the logs:

```bash
docker compose logs amide
```

Common issues:
- Port conflict: change `AMIDE_PORT`
- Permissions: ensure `./data` folder is writable
- Corrupted database: delete `./data/amide.db` and restart

## Security Notes

### Production Setup

1. **Generate a new SECRET_KEY:**

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

   Add to `.env`:

   ```bash
   SECRET_KEY=your-generated-key-here
   ENVIRONMENT=production
   ```

2. **Use PostgreSQL** instead of SQLite for multi-user deployments

3. **Run behind a reverse proxy** (nginx, traefik) with HTTPS

4. **Regularly backup** the `./data` folder

5. **Keep Docker images updated:**

   ```bash
   docker compose pull
   docker compose up -d
   ```

## What Amide Tracks

### Inventory Management

- **Peptides & Medicines**: Lyophilized items with vial sizes
- **BAC Water**: Reconstitution medium
- **Supplies**: Syringes, alcohol pads, etc.
- **Active Vials**: Reconstituted items with expiration dates

### Dosing

- **Protocols**: Multi-week dosing schedules
- **Dose Logging**: Track adherence and timing
- **Reconstitution**: Calculate concentrations and volumes
- **Cost Analysis**: Per-vial and per-dose pricing

### Metrics

- **Body Measurements**: Weight, body fat, blood pressure
- **Workouts**: Exercise tracking and logs
- **Water Intake**: Daily hydration goals
- **Sharing**: Multi-user access with permissions

## Support

For issues or questions, check the GitHub repository or refer to the main README.

---

**Last Updated**: 2026-10-02  
**Version**: Current (as of last commit)
