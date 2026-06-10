# Running Weather Recommendation Dashboard

## Project Overview

The Running Weather Recommendation Dashboard is a data-driven application that helps users decide the best time of day to run outside. The app uses live weather and air quality data to calculate a weighted Run Score and recommend whether outdoor running conditions are ideal, acceptable, limited, or better suited for an indoor workout.

This project was created because many people want to exercise consistently but struggle to plan around weather, air quality, temperature, humidity, wind, and precipitation. Instead of requiring users to check multiple weather sources, this dashboard combines the most important environmental factors into one clear recommendation.

The goal is to make running decisions easier by answering questions like:

- What is the best time to run today?
- What are the top three running times for the selected day?
- Should I run outside or choose an indoor workout?
- What weather factors are helping or hurting the run score?

---

## Why This Project Was Created

Consistency is one of the hardest parts of fitness. Weather can make it difficult to know when it is safe or comfortable to run outside. This project reduces that guesswork by using weather and air quality data to recommend optimal running windows.

The application is especially useful for runners with limited free time because it quickly identifies the best available run times based on current and forecasted conditions.

---

## Main Features

- Browser geolocation to use the user's current location
- Open-Meteo Forecast API for weather data
- Open-Meteo Air Quality API for AQI and pollution data
- ETL pipeline that extracts, transforms, validates, and loads data
- PostgreSQL database hosted through Supabase
- SQLAlchemy database connection
- Incremental loading using PostgreSQL UPSERT logic
- Weighted Run Score calculation
- Best Run Time recommendation
- Top 3 Run Times recommendation
- Date filter for viewing different forecast days
- 7-day temperature forecast visualization
- Weather factor score breakdown
- Interactive Dash dashboard

---

## Technology Stack

- Python
- Dash
- Plotly
- Pandas
- Requests
- SQLAlchemy
- Supabase
- Open-Meteo Forecast API
- Open-Meteo Air Quality API

---

## APIs Used

### Open-Meteo Forecast API

Used to retrieve hourly weather forecast data, including:

- Temperature
- Relative humidity
- Apparent temperature
- Precipitation probability
- Wind speed
- UV index
- Daylight indicator

### Open-Meteo Air Quality API

Used to retrieve air quality data, including:

- US AQI
- PM10
- PM2.5

---

## ETL Pipeline

### Extract

The application extracts data from two Open-Meteo APIs:

1. Open-Meteo Forecast API
2. Open-Meteo Air Quality API

The user's latitude and longitude are collected through browser geolocation and used as request parameters for both APIs.

### Transform

The pipeline transforms the raw API responses by:

- Converting JSON responses into Pandas DataFrames
- Standardizing column names
- Converting timestamps into datetime format
- Merging weather and air quality data by timestamp
- Handling missing values
- Removing duplicate timestamps
- Calculating weather factor scores
- Calculating the final weighted Run Score
- Creating an outdoor/indoor recommendation

### Load

The transformed dataset is loaded into a Supabase PostgreSQL table called `weather_hourly`.

The load process uses an incremental UPSERT strategy:

- New timestamps are inserted
- Existing timestamps are updated
- Duplicate hourly records are prevented

---

## Run Score Logic

The Run Score is calculated using weighted environmental factors.

| Factor | Weight |
|---|---:|
| Temperature | 25% |
| Humidity | 20% |
| Precipitation | 20% |
| Wind Speed | 15% |
| AQI | 15% |
| UV Index | 5% |

The final Run Score is on a 0–100 scale.

---

## Recommendation Categories

| Run Score | Recommendation |
|---:|---|
| 90–100 | Perfect Conditions for Outside |
| 50–89 | Outside Recommended |
| 30–49 | Short Outdoor Activity Only |
| 0–29 | Indoor Workout Recommended |

The dashboard excludes 10 PM through 3 AM from best-time recommendations so that suggested run times are more practical for most users.

---

## Dashboard Components

The Dash dashboard includes:

### KPI Cards

- Best Run Score
- Best Run Time
- Top 3 Run Times
- Indoor/Outdoor Recomendation

### Visualizations

- Run Score Over Time
- Temperature Forecast for Next 7 Days
- Average Weather Factor Scores

### Interactive Features

- Date filter
- Location refresh button
- Dynamic dashboard updates without restarting the app

---

## Project Structure

```text
Pipeline to Insights/
│
├── App.py
├── requirements.txt
├── .env
├── README.md
```

---

## Setup Instructions

### 1. Clone the Repository

```bash
git clone <your-github-repository-url>
cd "Pipeline to Insights"
```

If you already have the project folder on your computer, open it directly in VS Code.

---

### 2. Install Required Packages

Run this command in the VS Code terminal:

```bash
python -m pip install -r requirements.txt
```

If needed, the main packages can also be installed manually:

```bash
pip install dash dash-bootstrap-components pandas plotly requests sqlalchemy psycopg2-binary python-dotenv urllib3
```

---

### 3. Create the `.env` File

Create a file named `.env` in the same folder as `App.py`.

Add your Supabase PostgreSQL connection string:

```text
SUPABASE_DB_URL=postgresql+psycopg2://username:password@host:5432/postgres
```

Example format:

```text
SUPABASE_DB_URL=postgresql+psycopg2://postgres.projectref:your_password@aws-1-us-east-1.pooler.supabase.com:5432/postgres
```
---

### 4. Run the Dash App

In the terminal, run:

```bash
python App.py
```

Then open the local dashboard URL in your browser:

```text
http://127.0.0.1:8050
```

---

### 5. Use the Dashboard

1. Click **Use My Location & Refresh ETL**
2. Allow browser location access
3. Wait for the ETL pipeline to complete
4. Review the KPI cards, charts, and table
5. Use the date filter to view different forecast days

---

## Database Table

The main PostgreSQL table is:

```text
weather_hourly
```

It stores:

- Weather values
- Air quality values
- Scoring components
- Final Run Score
- Recommendation category

The `time` column is used as the primary key.

---

## Validation Checks

The ETL pipeline includes:

- API response validation
- Null value checks
- Duplicate timestamp detection
- Schema validation
- Range validation
- PostgreSQL row count verification

---

## Business Insights

The dashboard helps users make better fitness decisions by combining multiple environmental factors into one easy-to-understand score.

Key insights include:

- Identifying the best time to run on a selected day
- Comparing hourly run scores
- Understanding how temperature changes over the next 7 days
- Seeing which weather factors are lowering or improving running conditions
- Avoiding impractical recommendations during late-night hours
- Showing a user-facing recommendation instead of developer-only record counts



