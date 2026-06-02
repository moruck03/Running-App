
# app.py

import os
import json
import logging
from pathlib import Path

import requests
import pandas as pd
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from sqlalchemy import create_engine, text

from dash import Dash, html, dcc, Input, Output, State, callback, no_update
import dash_bootstrap_components as dbc


# =========================================================
# LOAD ENVIRONMENT VARIABLES
# =========================================================

load_dotenv()

SUPABASE_DB_URL = os.getenv(
    "SUPABASE_DB_URL",
    "postgresql+psycopg2://m0ruck03:PIPELINES@db.kfunmzwtpteokgwmuujm.supabase.co:5432/postgres"
)

engine = create_engine(SUPABASE_DB_URL)


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


# =========================================================
# RAW DATA STORAGE
# =========================================================

RAW_DATA_DIR = Path("raw_api_responses")
RAW_DATA_DIR.mkdir(exist_ok=True)


def save_raw_response(data, filename):
    file_path = RAW_DATA_DIR / filename

    with open(file_path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)

    logger.info(f"Saved raw API response to {file_path}")


# =========================================================
# RETRY SESSION
# =========================================================

def create_retry_session():
    session = requests.Session()

    retry_strategy = Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )

    adapter = HTTPAdapter(max_retries=retry_strategy)

    session.mount("https://", adapter)
    session.mount("http://", adapter)

    return session


# =========================================================
# API RESPONSE VALIDATION
# =========================================================

def validate_api_response(data, expected_key, api_name):
    if not data:
        raise ValueError(f"{api_name} failed: empty API response.")

    if expected_key not in data:
        raise ValueError(f"{api_name} failed: missing expected key '{expected_key}'.")

    if not data[expected_key]:
        raise ValueError(f"{api_name} failed: no records found in '{expected_key}'.")

    logger.info(f"{api_name} validation passed.")


# =========================================================
# EXTRACT: OPEN-METEO WEATHER API
# =========================================================

def extract_weather_data(latitude, longitude):
    logger.info("Extracting weather data from Open-Meteo Forecast API.")

    url = "https://api.open-meteo.com/v1/forecast"

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": ",".join([
            "temperature_2m",
            "relative_humidity_2m",
            "apparent_temperature",
            "precipitation_probability",
            "wind_speed_10m",
            "uv_index",
            "is_day"
        ]),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "auto"
    }

    session = create_retry_session()
    response = session.get(url, params=params, timeout=30)
    response.raise_for_status()

    data = response.json()

    validate_api_response(data, "hourly", "Open-Meteo Weather API")
    save_raw_response(data, "open_meteo_weather_raw.json")

    hourly = data["hourly"]

    return pd.DataFrame({
        "time": pd.to_datetime(hourly["time"]),
        "temperature_2m_f": hourly.get("temperature_2m"),
        "humidity_percent": hourly.get("relative_humidity_2m"),
        "apparent_temperature_f": hourly.get("apparent_temperature"),
        "precipitation_probability_percent": hourly.get("precipitation_probability"),
        "wind_speed_mph": hourly.get("wind_speed_10m"),
        "uv_index": hourly.get("uv_index"),
        "is_day": hourly.get("is_day")
    })


# =========================================================
# EXTRACT: OPEN-METEO AIR QUALITY API
# =========================================================

def extract_air_quality_data(latitude, longitude):
    logger.info("Extracting air quality data from Open-Meteo Air Quality API.")

    url = "https://air-quality-api.open-meteo.com/v1/air-quality"

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": ",".join([
            "us_aqi",
            "pm10",
            "pm2_5"
        ]),
        "timezone": "auto"
    }

    session = create_retry_session()
    response = session.get(url, params=params, timeout=30)
    response.raise_for_status()

    data = response.json()

    validate_api_response(data, "hourly", "Open-Meteo Air Quality API")
    save_raw_response(data, "open_meteo_air_quality_raw.json")

    hourly = data["hourly"]

    return pd.DataFrame({
        "time": pd.to_datetime(hourly["time"]),
        "aqi": hourly.get("us_aqi"),
        "pm10": hourly.get("pm10"),
        "pm25": hourly.get("pm2_5")
    })


# =========================================================
# CREATE POSTGRES TABLE
# =========================================================

def create_table():
    logger.info("Creating PostgreSQL table if needed.")

    with engine.begin() as conn:
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS weather_hourly (
            time TIMESTAMP PRIMARY KEY,
            temperature_2m_f DOUBLE PRECISION,
            humidity_percent DOUBLE PRECISION,
            apparent_temperature_f DOUBLE PRECISION,
            precipitation_probability_percent DOUBLE PRECISION,
            wind_speed_mph DOUBLE PRECISION,
            uv_index DOUBLE PRECISION,
            is_day BOOLEAN,
            aqi DOUBLE PRECISION,
            pm10 DOUBLE PRECISION,
            pm25 DOUBLE PRECISION,
            temperature_score INTEGER,
            humidity_score INTEGER,
            precipitation_score INTEGER,
            wind_score INTEGER,
            aqi_score INTEGER,
            uv_score INTEGER,
            run_score DOUBLE PRECISION,
            recommendation TEXT
        );
        """))


# =========================================================
# SCORING FUNCTIONS
# =========================================================

def temperature_score(temp):
    if temp < 32:
        return 30
    elif 40 <= temp <= 49:
        return 85
    elif 50 <= temp <= 65:
        return 100
    elif 76 <= temp <= 85:
        return 55
    elif temp > 85:
        return 25
    return 70


def humidity_score(humidity):
    if humidity < 55:
        return 100
    elif 55 <= humidity <= 60:
        return 85
    elif 61 <= humidity <= 65:
        return 70
    elif humidity > 70:
        return 20
    return 50


def precipitation_score(precip):
    if precip <= 10:
        return 100
    elif precip <= 30:
        return 85
    elif precip <= 50:
        return 60
    elif precip <= 70:
        return 35
    return 10


def wind_score(wind):
    if wind < 10:
        return 100
    elif wind <= 15:
        return 80
    elif wind <= 20:
        return 60
    elif wind <= 30:
        return 35
    return 10


def aqi_score(aqi):
    if aqi <= 50:
        return 100
    elif aqi <= 100:
        return 80
    elif aqi <= 150:
        return 45
    return 10


def uv_score(uv):
    if uv <= 2:
        return 100
    elif uv <= 5:
        return 85
    elif uv <= 7:
        return 65
    elif uv <= 10:
        return 40
    return 20


# =========================================================
# TRANSFORM
# =========================================================

def transform_data(weather_df, air_df):
    logger.info("Transforming and cleaning API data.")

    df = pd.merge(weather_df, air_df, on="time", how="left")
    df = df.drop_duplicates(subset=["time"])

    df = df.fillna({
        "precipitation_probability_percent": 0,
        "uv_index": 0,
        "aqi": 50,
        "pm10": 0,
        "pm25": 0,
        "is_day": 0
    })

    df["is_day"] = df["is_day"].astype(bool)

    df["temperature_score"] = df["temperature_2m_f"].apply(temperature_score)
    df["humidity_score"] = df["humidity_percent"].apply(humidity_score)
    df["precipitation_score"] = df["precipitation_probability_percent"].apply(precipitation_score)
    df["wind_score"] = df["wind_speed_mph"].apply(wind_score)
    df["aqi_score"] = df["aqi"].apply(aqi_score)
    df["uv_score"] = df["uv_index"].apply(uv_score)

    df["run_score"] = (
        (df["temperature_score"] * 0.30) +
        (df["humidity_score"] * 0.25) +
        (df["precipitation_score"] * 0.20) +
        (df["wind_score"] * 0.10) +
        (df["aqi_score"] * 0.10) +
        (df["uv_score"] * 0.05)
    )

    def recommendation(score):
        if score >= 90:
            return "Perfect Conditions for Outside"
        elif score >= 50:
            return "Outside Recommended"
        elif score >= 30:
            return "Short Outdoor Activity Only"
        return "Indoor Workout Recommended"

    df["recommendation"] = df["run_score"].apply(recommendation)

    return df[[
        "time",
        "temperature_2m_f",
        "humidity_percent",
        "apparent_temperature_f",
        "precipitation_probability_percent",
        "wind_speed_mph",
        "uv_index",
        "is_day",
        "aqi",
        "pm10",
        "pm25",
        "temperature_score",
        "humidity_score",
        "precipitation_score",
        "wind_score",
        "aqi_score",
        "uv_score",
        "run_score",
        "recommendation"
    ]]


# =========================================================
# VALIDATION
# =========================================================

def validate_dataframe(df, name):
    if df.empty:
        raise ValueError(f"{name} failed: dataframe is empty.")

    duplicate_count = df.duplicated(subset=["time"]).sum()

    if duplicate_count > 0:
        logger.warning(f"{name}: {duplicate_count} duplicate timestamps found.")
    else:
        logger.info(f"{name}: no duplicate timestamps found.")

    nulls = df.isnull().sum()
    nulls = nulls[nulls > 0]

    if not nulls.empty:
        logger.warning(f"{name}: null values found:\n{nulls}")
    else:
        logger.info(f"{name}: no null values found.")

    logger.info(f"{name}: {len(df)} rows available.")


def validate_schema(df):
    expected_columns = {
        "time",
        "temperature_2m_f",
        "humidity_percent",
        "apparent_temperature_f",
        "precipitation_probability_percent",
        "wind_speed_mph",
        "uv_index",
        "is_day",
        "aqi",
        "pm10",
        "pm25",
        "temperature_score",
        "humidity_score",
        "precipitation_score",
        "wind_score",
        "aqi_score",
        "uv_score",
        "run_score",
        "recommendation"
    }

    missing = expected_columns - set(df.columns)

    if missing:
        raise ValueError(f"Schema validation failed. Missing columns: {missing}")

    logger.info("Schema validation passed.")


def validate_ranges(df):
    range_checks = {
        "temperature_2m_f": (-50, 130),
        "humidity_percent": (0, 100),
        "precipitation_probability_percent": (0, 100),
        "wind_speed_mph": (0, 200),
        "uv_index": (0, 15),
        "aqi": (0, 500),
        "pm10": (0, 1000),
        "pm25": (0, 1000),
        "run_score": (0, 100)
    }

    for column, (minimum, maximum) in range_checks.items():
        invalid = df[(df[column] < minimum) | (df[column] > maximum)]

        if not invalid.empty:
            raise ValueError(
                f"Range validation failed for {column}: {len(invalid)} invalid rows."
            )

    logger.info("Range validation passed.")


# =========================================================
# LOAD WITH INCREMENTAL UPSERT
# =========================================================

def load_to_postgres(df):
    logger.info("Loading transformed data into PostgreSQL.")

    records = df.to_dict(orient="records")

    upsert_sql = text("""
        INSERT INTO weather_hourly (
            time,
            temperature_2m_f,
            humidity_percent,
            apparent_temperature_f,
            precipitation_probability_percent,
            wind_speed_mph,
            uv_index,
            is_day,
            aqi,
            pm10,
            pm25,
            temperature_score,
            humidity_score,
            precipitation_score,
            wind_score,
            aqi_score,
            uv_score,
            run_score,
            recommendation
        )
        VALUES (
            :time,
            :temperature_2m_f,
            :humidity_percent,
            :apparent_temperature_f,
            :precipitation_probability_percent,
            :wind_speed_mph,
            :uv_index,
            :is_day,
            :aqi,
            :pm10,
            :pm25,
            :temperature_score,
            :humidity_score,
            :precipitation_score,
            :wind_score,
            :aqi_score,
            :uv_score,
            :run_score,
            :recommendation
        )
        ON CONFLICT (time)
        DO UPDATE SET
            temperature_2m_f = EXCLUDED.temperature_2m_f,
            humidity_percent = EXCLUDED.humidity_percent,
            apparent_temperature_f = EXCLUDED.apparent_temperature_f,
            precipitation_probability_percent = EXCLUDED.precipitation_probability_percent,
            wind_speed_mph = EXCLUDED.wind_speed_mph,
            uv_index = EXCLUDED.uv_index,
            is_day = EXCLUDED.is_day,
            aqi = EXCLUDED.aqi,
            pm10 = EXCLUDED.pm10,
            pm25 = EXCLUDED.pm25,
            temperature_score = EXCLUDED.temperature_score,
            humidity_score = EXCLUDED.humidity_score,
            precipitation_score = EXCLUDED.precipitation_score,
            wind_score = EXCLUDED.wind_score,
            aqi_score = EXCLUDED.aqi_score,
            uv_score = EXCLUDED.uv_score,
            run_score = EXCLUDED.run_score,
            recommendation = EXCLUDED.recommendation;
    """)

    with engine.begin() as conn:
        conn.execute(upsert_sql, records)

    logger.info(f"Loaded or updated {len(records)} records.")


def validate_postgres_load(expected_rows):
    with engine.begin() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM weather_hourly;")).scalar()

    if count < expected_rows:
        raise ValueError(
            f"Load validation failed. Expected at least {expected_rows}, found {count}."
        )

    logger.info(f"Load validation passed. weather_hourly contains {count} rows.")


def get_best_run_times(limit=5):
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT
                time,
                run_score,
                recommendation,
                temperature_2m_f,
                humidity_percent,
                precipitation_probability_percent,
                wind_speed_mph,
                aqi,
                uv_index
            FROM weather_hourly
            ORDER BY run_score DESC, time ASC
            LIMIT :limit;
        """), {"limit": limit}).fetchall()

    return rows


# =========================================================
# FULL ETL FUNCTION CALLED BY DASH
# =========================================================

def run_etl_pipeline(latitude, longitude):
    logger.info("Starting ETL pipeline from Dash coordinates.")

    create_table()

    weather_df = extract_weather_data(latitude, longitude)
    validate_dataframe(weather_df, "Weather DataFrame")

    air_df = extract_air_quality_data(latitude, longitude)
    validate_dataframe(air_df, "Air Quality DataFrame")

    final_df = transform_data(weather_df, air_df)

    validate_schema(final_df)
    validate_ranges(final_df)
    validate_dataframe(final_df, "Final Transformed DataFrame")

    load_to_postgres(final_df)
    validate_postgres_load(len(final_df))

    best_times = get_best_run_times(limit=5)

    logger.info("ETL pipeline completed successfully.")

    return best_times


# =========================================================
# DASH APP
# =========================================================

app = Dash(__name__, external_stylesheets=[dbc.themes.BOOTSTRAP])

app.layout = dbc.Container([
    html.H1("Running Weather Recommendation App"),

    html.P(
        "Click the button below to allow location access. "
        "Dash will use your browser coordinates to call both Open-Meteo APIs."
    ),

    dbc.Button(
        "Use My Location & Run ETL",
        id="get-location-button",
        color="primary",
        className="mb-3"
    ),

    dcc.Store(id="location-store"),

    html.Div(id="location-output", className="mb-3"),

    html.Div(id="etl-output")
], fluid=True)


# =========================================================
# CLIENTSIDE CALLBACK: BROWSER GEOLOCATION
# =========================================================

app.clientside_callback(
    """
    function(n_clicks) {
        if (!n_clicks) {
            return window.dash_clientside.no_update;
        }

        return new Promise(function(resolve, reject) {
            if (!navigator.geolocation) {
                resolve({
                    error: "Geolocation is not supported by this browser."
                });
            }

            navigator.geolocation.getCurrentPosition(
                function(position) {
                    resolve({
                        latitude: position.coords.latitude,
                        longitude: position.coords.longitude
                    });
                },
                function(error) {
                    resolve({
                        error: "Location permission denied or unavailable."
                    });
                }
            );
        });
    }
    """,
    Output("location-store", "data"),
    Input("get-location-button", "n_clicks")
)


# =========================================================
# SERVER CALLBACK: RUN ETL AFTER COORDINATES ARE RECEIVED
# =========================================================

@callback(
    Output("location-output", "children"),
    Output("etl-output", "children"),
    Input("location-store", "data")
)
def run_pipeline_from_location(location_data):
    if not location_data:
        return "", ""

    if "error" in location_data:
        return dbc.Alert(location_data["error"], color="danger"), ""

    latitude = location_data["latitude"]
    longitude = location_data["longitude"]

    location_message = dbc.Alert(
        f"Location received: latitude {latitude:.4f}, longitude {longitude:.4f}",
        color="success"
    )

    try:
        best_times = run_etl_pipeline(latitude, longitude)

        rows = []

        for row in best_times:
            rows.append(html.Tr([
                html.Td(str(row.time)),
                html.Td(round(row.run_score, 2)),
                html.Td(row.recommendation),
                html.Td(row.temperature_2m_f),
                html.Td(row.humidity_percent),
                html.Td(row.precipitation_probability_percent),
                html.Td(row.wind_speed_mph),
                html.Td(row.aqi),
                html.Td(row.uv_index)
            ]))

        table = dbc.Table([
            html.Thead(html.Tr([
                html.Th("Time"),
                html.Th("Run Score"),
                html.Th("Recommendation"),
                html.Th("Temp °F"),
                html.Th("Humidity %"),
                html.Th("Precip %"),
                html.Th("Wind MPH"),
                html.Th("AQI"),
                html.Th("UV")
            ])),
            html.Tbody(rows)
        ], bordered=True, hover=True, responsive=True)

        return location_message, html.Div([
            dbc.Alert("ETL pipeline completed successfully.", color="success"),
            html.H3("Top 5 Best Run Times"),
            table
        ])

    except Exception as error:
        logger.error(f"Dash ETL callback failed: {error}")

        return location_message, dbc.Alert(
            f"ETL pipeline failed: {error}",
            color="danger"
        )


# =========================================================
# RUN APP
# =========================================================

if __name__ == "__main__":
    app.run(debug=True)

