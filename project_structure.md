# One Piece TCG Price Tracker - Backend

## Overview
A data pipeline that scrapes eBay sold listings for One Piece Trading Card Game products, stores raw data in S3, transforms/classifies the data via ETL, and loads clean structured data into PostgreSQL for frontend consumption.

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   eBay API  │────▶│  Scraper    │────▶│  S3 (Raw)   │────▶│  ETL Jobs   │
│  (Sold      │     │  Service    │     │  Storage    │     │  (Transform │
│  Listings)  │     │             │     │             │     │  + Classify)│
└─────────────┘     └─────────────┘     └─────────────┘     └──────┬──────┘
                                                                   │
                           ┌───────────────────────────────────────┘
                           ▼
                    ┌─────────────┐     ┌─────────────┐
                    │  PostgreSQL │────▶│  REST API   │────▶ Frontend
                    │  (RDS)      │     │  (Future)   │
                    └─────────────┘     └─────────────┘

Orchestration: Apache Airflow (MWAA or self-hosted on EC2/ECS)
```

## Tech Stack

| Component       | Technology                          | Notes                                    |
|-----------------|-------------------------------------|------------------------------------------|
| Language        | Python 3.11+                        | Primary language for all backend code    |
| Scraping        | `requests` + `BeautifulSoup` / `httpx` | eBay sold listings scraper            |
| Raw Storage     | AWS S3                              | JSON/Parquet files, partitioned by date  |
| Database        | PostgreSQL 15+ (AWS RDS)            | Clean, queryable data                    |
| ORM             | SQLAlchemy 2.0                      | Database models and queries              |
| ETL             | Python scripts / Pandas             | Transform and classify listings          |
| Orchestration   | Apache Airflow (AWS MWAA preferred) | DAGs for daily/backfill jobs             |
| Infrastructure  | Terraform (optional)                | IaC for AWS resources                    |
| Containerization| Docker                              | Local dev and deployment                 |

## Directory Structure

```
optcg-price-tracker/
├── README.md
├── project_structure.md          # This file - AI/dev context
├── pyproject.toml                # Project dependencies (Poetry/uv)
├── requirements.txt              # Fallback deps
├── .env.example                  # Environment variable template
├── docker-compose.yml            # Local Postgres + Airflow for dev
├── Dockerfile                    # Container for scraper/ETL jobs
│
├── src/
│   ├── __init__.py
│   │
│   ├── scraper/                  # eBay scraping module
│   │   ├── __init__.py
│   │   ├── ebay_client.py        # HTTP client for eBay requests
│   │   ├── parser.py             # Parse HTML/JSON responses
│   │   ├── sold_listings.py      # Main scraper logic for sold items
│   │   └── rate_limiter.py       # Respect eBay rate limits
│   │
│   ├── storage/                  # S3 raw data storage
│   │   ├── __init__.py
│   │   ├── s3_client.py          # Upload/download raw data
│   │   └── schemas.py            # Raw data JSON schema definitions
│   │
│   ├── etl/                      # Extract, Transform, Load
│   │   ├── __init__.py
│   │   ├── extract.py            # Read from S3
│   │   ├── transform.py          # Main transformation logic
│   │   ├── classifiers/          # Product classification logic
│   │   │   ├── __init__.py
│   │   │   ├── product_type.py   # Sealed vs Raw vs Graded
│   │   │   ├── sealed_parser.py  # Parse sealed product titles
│   │   │   ├── card_parser.py    # Parse single card titles
│   │   │   └── graded_parser.py  # Parse graded card titles (PSA, BGS, CGC)
│   │   ├── normalizers/          # Data cleaning/normalization
│   │   │   ├── __init__.py
│   │   │   ├── title_cleaner.py  # Remove noise from titles
│   │   │   ├── set_matcher.py    # Match to known OP sets
│   │   │   └── card_matcher.py   # Match to known card names
│   │   └── load.py               # Load to PostgreSQL
│   │
│   ├── db/                       # Database layer
│   │   ├── __init__.py
│   │   ├── connection.py         # SQLAlchemy engine/session
│   │   ├── models.py             # ORM models (Sales, Cards, Sets, etc.)
│   │   └── migrations/           # Alembic migrations
│   │       ├── env.py
│   │       └── versions/
│   │
│   ├── reference_data/           # Static reference data
│   │   ├── __init__.py
│   │   ├── sets.py               # OP01, OP02, ... OP13 definitions
│   │   ├── product_types.py      # Booster box, pack, starter deck, etc.
│   │   └── grading_companies.py  # PSA, BGS, CGC mappings
│   │
│   └── utils/                    # Shared utilities
│       ├── __init__.py
│       ├── config.py             # Load env vars / settings
│       ├── logging.py            # Structured logging setup
│       └── exceptions.py         # Custom exceptions
│
├── airflow/                      # Airflow DAGs and config
│   ├── dags/
│   │   ├── daily_scrape.py       # Daily incremental scrape DAG
│   │   ├── backfill_scrape.py    # Historical 3-month backfill DAG
│   │   └── etl_pipeline.py       # S3 -> Transform -> Postgres DAG
│   └── plugins/                  # Custom Airflow operators if needed
│
├── scripts/                      # Standalone scripts
│   ├── run_backfill.py           # One-time historical data load
│   ├── run_daily.py              # Manual daily run (outside Airflow)
│   └── seed_reference_data.py    # Populate sets/cards reference tables
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py               # Pytest fixtures
│   ├── test_scraper/
│   ├── test_etl/
│   │   ├── test_classifiers.py   # Test product classification
│   │   └── test_normalizers.py   # Test title parsing
│   └── test_db/
│
├── data/                         # Local dev data (gitignored)
│   ├── raw/                      # Sample raw JSON files
│   └── processed/                # Sample transformed data
│
└── infrastructure/               # IaC (optional)
    └── terraform/
        ├── main.tf
        ├── s3.tf
        ├── rds.tf
        └── mwaa.tf               # Managed Airflow
```

## Data Models

### Raw Data (S3)
Stored as JSON files, partitioned by scrape date:
```
s3://optcg-raw-data/
└── sold_listings/
    └── year=2024/
        └── month=01/
            └── day=15/
                └── scrape_123456.json
```

**Raw listing schema:**
```json
{
  "listing_id": "123456789",
  "title": "One Piece OP01-121 Monkey D. Luffy SEC Romance Dawn English NM",
  "price": 89.99,
  "shipping_price": 4.99,
  "sold_date": "2024-01-15T14:30:00Z",
  "listing_url": "https://www.ebay.com/itm/123456789",
  "seller": "seller_username",
  "scraped_at": "2024-01-15T18:00:00Z"
}
```

### PostgreSQL Schema

**Core Tables:**

```sql
-- Reference: One Piece TCG Sets
CREATE TABLE sets (
    id SERIAL PRIMARY KEY,
    code VARCHAR(10) UNIQUE NOT NULL,     -- e.g., "OP01", "OP05"
    name VARCHAR(100) NOT NULL,            -- e.g., "Romance Dawn"
    release_date DATE,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Reference: Known cards (optional, for matching)
CREATE TABLE cards (
    id SERIAL PRIMARY KEY,
    set_id INTEGER REFERENCES sets(id),
    card_number VARCHAR(20),               -- e.g., "OP01-121"
    name VARCHAR(200),                     -- e.g., "Monkey D. Luffy"
    rarity VARCHAR(20),                    -- SEC, SR, R, UC, C
    created_at TIMESTAMP DEFAULT NOW()
);

-- Main sales data table
CREATE TABLE sales (
    id SERIAL PRIMARY KEY,
    
    -- Raw data reference
    raw_s3_path VARCHAR(500),
    ebay_listing_id VARCHAR(50) UNIQUE,
    listing_url VARCHAR(500),
    
    -- Cleaned/extracted fields
    product_type VARCHAR(20) NOT NULL,     -- 'raw', 'graded', 'sealed'
    
    -- For cards (raw or graded)
    card_name VARCHAR(200),
    set_id INTEGER REFERENCES sets(id),
    card_number VARCHAR(20),
    
    -- For graded cards
    grading_company VARCHAR(20),           -- 'PSA', 'BGS', 'CGC', etc.
    grade DECIMAL(3,1),                    -- 10, 9.5, 9, etc.
    
    -- For sealed products
    sealed_type VARCHAR(50),               -- 'booster_box', 'pack', 'starter_deck'
    
    -- Pricing
    sale_price DECIMAL(10,2) NOT NULL,
    shipping_price DECIMAL(10,2),
    total_price DECIMAL(10,2) GENERATED ALWAYS AS (sale_price + COALESCE(shipping_price, 0)) STORED,
    
    -- Metadata
    sold_date TIMESTAMP NOT NULL,
    original_title TEXT,                   -- Preserve original for debugging
    cleaned_title TEXT,
    
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Indexes for common queries
CREATE INDEX idx_sales_set_id ON sales(set_id);
CREATE INDEX idx_sales_product_type ON sales(product_type);
CREATE INDEX idx_sales_sold_date ON sales(sold_date);
CREATE INDEX idx_sales_card_number ON sales(card_number);
CREATE INDEX idx_sales_grading ON sales(grading_company, grade);
```

## Product Classification Logic

### Product Types
The ETL classifier determines product type from the title:

| Type    | Indicators in Title                                      |
|---------|----------------------------------------------------------|
| Graded  | "PSA", "BGS", "CGC", "SGC" + numeric grade               |
| Sealed  | "booster box", "booster pack", "starter deck", "display" |
| Raw     | Default - single cards without grading                   |

### Title Parsing Examples

**Graded Card:**
```
Input:  "PSA 10 One Piece OP01-121 Monkey D. Luffy SEC Romance Dawn"
Output: {
    product_type: "graded",
    grading_company: "PSA",
    grade: 10.0,
    card_name: "Monkey D. Luffy",
    card_number: "OP01-121",
    set_code: "OP01"
}
```

**Raw Card:**
```
Input:  "One Piece OP05-119 Monkey D. Luffy Gear 5 SEC Awakening of the New Era"
Output: {
    product_type: "raw",
    card_name: "Monkey D. Luffy Gear 5",
    card_number: "OP05-119",
    set_code: "OP05"
}
```

**Sealed Product:**
```
Input:  "One Piece TCG OP-01 Romance Dawn Booster Box English Sealed"
Output: {
    product_type: "sealed",
    sealed_type: "booster_box",
    set_code: "OP01"
}
```

## One Piece TCG Set Reference

| Code  | Name                          | Notes                    |
|-------|-------------------------------|--------------------------|
| OP01  | Romance Dawn                  | First English set        |
| OP02  | Paramount War                 |                          |
| OP03  | Pillars of Strength           |                          |
| OP04  | Kingdoms of Intrigue          |                          |
| OP05  | Awakening of the New Era      | Gear 5 Luffy chase card  |
| OP06  | Wings of the Captain          |                          |
| OP07  | 500 Years in the Future       |                          |
| OP08  | Two Legends                   |                          |
| OP09  | The Four Emperors             |                          |
| OP10  | Royal Blood                   |                          |
| OP11  | Emperors in the New World     |                          |
| OP12  | TBD                           |                          |
| OP13  | TBD                           |                          |
| ST01-ST18 | Starter Decks              | Various starter products |
| PRB01 | Premium Booster              |                          |
| EB01  | Extra Booster: Memorial Collection |                    |

## Airflow DAGs

### 1. Daily Scrape DAG (`daily_scrape.py`)
**Schedule:** Daily at 6 AM UTC
**Tasks:**
1. Scrape eBay sold listings from past 24 hours
2. Save raw JSON to S3
3. Trigger ETL pipeline

### 2. ETL Pipeline DAG (`etl_pipeline.py`)
**Schedule:** Triggered by scrape or manual
**Tasks:**
1. Extract: Read new files from S3
2. Transform: Classify and parse listings
3. Load: Upsert to PostgreSQL

### 3. Backfill DAG (`backfill_scrape.py`)
**Schedule:** Manual trigger only
**Tasks:**
1. Scrape 3 months of historical data (eBay max)
2. Process in batches to avoid rate limits
3. Full ETL load

## Configuration

**Environment Variables (`.env`):**
```bash
# AWS
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_REGION=us-east-1
S3_BUCKET_RAW=optcg-raw-data

# Database
DATABASE_URL=postgresql://user:pass@host:5432/optcg
DB_HOST=
DB_PORT=5432
DB_NAME=optcg
DB_USER=
DB_PASSWORD=

# eBay (if using API)
EBAY_APP_ID=
EBAY_CERT_ID=

# Airflow
AIRFLOW_HOME=/opt/airflow
```

## Development Workflow

### Local Setup
```bash
# Clone and setup
git clone <repo>
cd optcg-price-tracker
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"

# Start local Postgres
docker-compose up -d postgres

# Run migrations
alembic upgrade head

# Seed reference data
python scripts/seed_reference_data.py

# Run tests
pytest
```

### Running Locally
```bash
# Manual scrape (small test)
python -m src.scraper.sold_listings --query "one piece tcg" --limit 100

# Run ETL on local file
python -m src.etl.transform --input data/raw/sample.json

# Full daily pipeline
python scripts/run_daily.py
```

## Key Design Decisions

1. **S3 for raw data**: Keeps original data immutable; allows reprocessing if ETL logic changes
2. **Separate classifiers**: Modular parsing for each product type; easy to improve individually
3. **Reference tables**: Pre-populated set/card data improves matching accuracy
4. **Upsert on listing_id**: Prevents duplicates if same listing scraped twice
5. **Generated total_price column**: Ensures consistent price calculations

## Future Considerations

- [ ] REST API layer (FastAPI) for frontend consumption
- [ ] Price trend aggregations (daily/weekly averages per card)
- [ ] Alert system for price drops on specific cards
- [ ] Card image storage and matching
- [ ] Multi-language support (JP cards have different set codes)

## Common Commands Reference

```bash
# Run specific scraper
python -m src.scraper.sold_listings --set OP05 --days 1

# Process S3 files from specific date
python -m src.etl.transform --date 2024-01-15

# Check classification accuracy
python -m src.etl.classifiers.product_type --test

# Database shell
psql $DATABASE_URL
```