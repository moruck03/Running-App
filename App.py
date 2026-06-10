# Running Weather Recommendation Dashboard
# Dash + Open-Meteo APIs + Supabase PostgreSQL + ETL Pipeline

import os
import json
import logging
from pathlib import Path

import requests
import pandas as pd
import plotly.express as px
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from sqlalchemy import create_engine, text

from dash import Dash, html, dcc, Input, Output, callback
import dash_bootstrap_components as dbc


# =========================================================
# LOAD ENVIRONMENT VARIABLES
# =========================================================

load_dotenv(override=True)

SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL")

if not SUPABASE_DB_URL:
    raise ValueError(
        "SUPABASE_DB_URL is missing. Check that your .env file exists "
        "and contains SUPABASE_DB_URL=your_connection_string"
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
    # Best running temperatures are usually cool to mild.
    # Full ranges are included so no temperature falls through unclear gaps.
    if temp < 32:
        return 30
    elif 32 <= temp <= 39:
        return 60
    elif 40 <= temp <= 49:
        return 85
    elif 50 <= temp <= 65:
        return 100
    elif 66 <= temp <= 75:
        return 80
    elif 76 <= temp <= 85:
        return 55
    else:
        return 25


def humidity_score(humidity):
    # Humidity is measured as a percentage.
    # Lower to moderate humidity is better for outdoor running.
    if humidity < 55:
        return 100
    elif 55 <= humidity <= 60:
        return 85
    elif 61 <= humidity <= 65:
        return 70
    elif 66 <= humidity <= 70:
        return 50
    else:
        return 20


def precipitation_score(precip):
    # Precipitation is the chance of precipitation as a percentage.
    if precip <= 10:
        return 100
    elif 11 <= precip <= 30:
        return 85
    elif 31 <= precip <= 50:
        return 60
    elif 51 <= precip <= 70:
        return 35
    else:
        return 10


def wind_score(wind):
    # Wind speed is measured in miles per hour.
    if wind < 10:
        return 100
    elif 10 <= wind <= 15:
        return 80
    elif 16 <= wind <= 20:
        return 60
    elif 21 <= wind <= 30:
        return 35
    else:
        return 10


def aqi_score(aqi):
    # AQI uses the U.S. AQI scale from Open-Meteo Air Quality API.
    if aqi <= 50:
        return 100
    elif 51 <= aqi <= 100:
        return 80
    elif 101 <= aqi <= 150:
        return 45
    else:
        return 10


def uv_score(uv):
    if uv <= 2:
        return 100
    elif 3 <= uv <= 5:
        return 85
    elif 6 <= uv <= 7:
        return 65
    elif 8 <= uv <= 10:
        return 40
    else:
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

    # Weighted run score:
    # Temperature: 25%
    # Humidity: 20%
    # Precipitation: 20%
    # Wind: 15%
    # AQI: 15%
    # UV Index: 5%
    df["run_score"] = (
        (df["temperature_score"] * 0.25) +
        (df["humidity_score"] * 0.20) +
        (df["precipitation_score"] * 0.20) +
        (df["wind_score"] * 0.15) +
        (df["aqi_score"] * 0.15) +
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


# =========================================================
# DATABASE QUERY HELPERS
# =========================================================

def get_available_dates():
    query = """
        SELECT DISTINCT DATE(time) AS run_date
        FROM weather_hourly
        ORDER BY run_date;
    """

    try:
        df = pd.read_sql(query, engine)
    except Exception:
        return []

    return [
        {"label": str(row["run_date"]), "value": str(row["run_date"])}
        for _, row in df.iterrows()
    ]


def get_today_string():
    return str(pd.Timestamp.now().date())


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
            WHERE DATE(time) = CURRENT_DATE
            ORDER BY run_score DESC, time ASC
            LIMIT :limit;
        """), {"limit": limit}).fetchall()

    return rows


def get_dashboard_data():
    query = """
        SELECT
            time,
            run_score,
            recommendation,
            temperature_2m_f,
            humidity_percent,
            precipitation_probability_percent,
            wind_speed_mph,
            aqi,
            uv_index,
            temperature_score,
            humidity_score,
            precipitation_score,
            wind_score,
            aqi_score,
            uv_score
        FROM weather_hourly
        ORDER BY time;
    """

    return pd.read_sql(query, engine)


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
# DASH APP LAYOUT
# =========================================================

app = Dash(__name__, external_stylesheets=[dbc.themes.BOOTSTRAP])

app.layout = dbc.Container([
    html.H1("Running Weather Recommendation Dashboard", className="text-center mt-4"),

    html.P(
        "This dashboard uses Open-Meteo weather and air quality data to recommend the best time to run.",
        className="text-center"
    ),

    dbc.Row([
        dbc.Col(
            dbc.Button(
                "Use My Location & Refresh ETL",
                id="get-location-button",
                color="primary",
                className="mb-3"
            ),
            width=12
        )
    ]),

    dcc.Store(id="location-store"),

    html.Div(id="location-output", className="mb-3"),

    dbc.Row([
        dbc.Col([
            html.Label("Filter by Date"),
            dcc.Dropdown(
                id="date-filter",
                options=[],
                value=None,
                clearable=False
            )
        ], width=4)
    ], className="mb-4"),

    dbc.Row([
        dbc.Col(dbc.Card([
            dbc.CardBody([
                html.H5("Best Run Score", className="card-title"),
                html.H2(id="best-score-card")
            ])
        ]), width=3),

        dbc.Col(dbc.Card([
            dbc.CardBody([
                html.H5("Best Run Time", className="card-title"),
                html.H2(id="best-time-card")
            ])
        ]), width=3),

        dbc.Col(dbc.Card([
            dbc.CardBody([
                html.H5("Top 3 Run Times", className="card-title"),
                html.Div(id="top-three-times-card")
            ])
        ]), width=3),

        dbc.Col(dbc.Card([
            dbc.CardBody([
                html.H5("Recommendation", className="card-title"),
                html.H2(id="recommendation-card")
            ])
        ]), width=3),
    ], className="mb-4"),

    dbc.Row([
        dbc.Col(dcc.Graph(id="run-score-line-chart"), width=12)
    ]),

    dbc.Row([
        dbc.Col(dcc.Graph(id="temperature-forecast-chart"), width=6),
        dbc.Col(dcc.Graph(id="score-factor-breakdown"), width=6),
    ]),

    html.H3("Filtered Running Conditions Table", className="mt-4"),
    html.Div(id="data-table")

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
    Input("location-store", "data")
)
def run_pipeline_from_location(location_data):
    if not location_data:
        return ""

    if "error" in location_data:
        return dbc.Alert(location_data["error"], color="danger")

    latitude = location_data["latitude"]
    longitude = location_data["longitude"]

    try:
        run_etl_pipeline(latitude, longitude)

        return dbc.Alert(
            f"ETL refreshed successfully for latitude {latitude:.4f}, longitude {longitude:.4f}.",
            color="success"
        )

    except Exception as error:
        logger.error(f"Dash ETL callback failed: {error}")

        return dbc.Alert(
            f"ETL pipeline failed: {error}",
            color="danger"
        )


# =========================================================
# SERVER CALLBACK: UPDATE DATE FILTER OPTIONS
# =========================================================

@callback(
    Output("date-filter", "options"),
    Output("date-filter", "value"),
    Input("location-output", "children")
)
def update_date_filter_options(_):
    options = get_available_dates()

    if not options:
        return [], None

    today = get_today_string()
    available_values = [option["value"] for option in options]

    if today in available_values:
        selected_value = today
    else:
        selected_value = available_values[0]

    return options, selected_value


# =========================================================
# SERVER CALLBACK: UPDATE DASHBOARD FROM POSTGRESQL
# =========================================================

@callback(
    Output("best-score-card", "children"),
    Output("best-time-card", "children"),
    Output("top-three-times-card", "children"),
    Output("recommendation-card", "children"),
    Output("run-score-line-chart", "figure"),
    Output("temperature-forecast-chart", "figure"),
    Output("score-factor-breakdown", "figure"),
    Output("data-table", "children"),
    Input("date-filter", "value"),
    Input("location-output", "children")
)
def update_dashboard(selected_date, _):
    try:
        df = get_dashboard_data()
    except Exception as error:
        logger.error(f"Dashboard query failed: {error}")

        empty_fig = px.line(title="Database query failed")

        return (
            "N/A",
            "N/A",
            "N/A",
            "N/A",
            empty_fig,
            empty_fig,
            empty_fig,
            dbc.Alert(f"Could not load dashboard data: {error}", color="danger")
        )

    if df.empty:
        empty_fig = px.line(title="No data available")

        return (
            "N/A",
            "N/A",
            "N/A",
            "N/A",
            empty_fig,
            empty_fig,
            empty_fig,
            dbc.Alert("No data available. Click 'Use My Location & Refresh ETL' first.", color="warning")
        )

    df["time"] = pd.to_datetime(df["time"])
    df["run_date"] = df["time"].dt.date.astype(str)

    temp_df = df.copy()
    temp_df = temp_df[
        temp_df["time"] < pd.Timestamp.now() + pd.Timedelta(days=7)
    ]

    if selected_date:
        filtered_df = df[df["run_date"] == selected_date]
    else:
        filtered_df = df[df["run_date"] == get_today_string()]

    # Exclude late-night/very-early hours from best-time recommendations.
    # Excluded hours: 10 PM, 11 PM, 12 AM, 1 AM, 2 AM, 3 AM.
    excluded_hours = [22, 23, 0, 1, 2, 3]
    filtered_df = filtered_df[
        ~filtered_df["time"].dt.hour.isin(excluded_hours)
    ]

    if filtered_df.empty:
        empty_fig = px.line(title="No data available for selected date")

        return (
            "N/A",
            "N/A",
            "N/A",
            "N/A",
            empty_fig,
            empty_fig,
            empty_fig,
            dbc.Alert("No records found for the selected date after excluding 10 PM through 3 AM.", color="warning")
        )

    best_row = filtered_df.sort_values("run_score", ascending=False).iloc[0]

    best_score = round(best_row["run_score"], 2)
    best_time = pd.to_datetime(best_row["time"]).strftime("%I:%M %p")
    current_recommendation = best_row["recommendation"]

    top_three = (
        filtered_df
        .sort_values("run_score", ascending=False)
        .head(3)
    )

    medals = ["🥇", "🥈", "🥉"]
    top_three_text = []

    for i, (_, row) in enumerate(top_three.iterrows()):
        run_time = pd.to_datetime(row["time"]).strftime("%I:%M %p")
        score = round(row["run_score"], 1)
        top_three_text.append(
            html.Div(f"{medals[i]} {run_time} — Score: {score}")
        )

    line_fig = px.line(
        filtered_df,
        x="time",
        y="run_score",
        title=f"Run Score Over Time for {selected_date}",
        labels={"time": "Time", "run_score": "Run Score"}
    )

    temp_fig = px.line(
        temp_df,
        x="time",
        y="temperature_2m_f",
        title="Temperature Forecast for Next 7 Days",
        labels={
            "time": "Date/Time",
            "temperature_2m_f": "Temperature °F"
        }
    )

    factor_cols = [
        "temperature_score",
        "humidity_score",
        "precipitation_score",
        "wind_score",
        "aqi_score",
        "uv_score"
    ]

    factor_avg = filtered_df[factor_cols].mean().reset_index()
    factor_avg.columns = ["factor", "average_score"]

    factor_fig = px.bar(
        factor_avg,
        x="factor",
        y="average_score",
        title=f"Average Weather Factor Scores for {selected_date}",
        labels={
            "factor": "Weather Factor",
            "average_score": "Average Score"
        }
    )

    display_df = filtered_df[[
        "time",
        "run_score",
        "recommendation",
        "temperature_2m_f",
        "humidity_percent",
        "precipitation_probability_percent",
        "wind_speed_mph",
        "aqi",
        "uv_index"
    ]].copy()

    display_df["time"] = display_df["time"].dt.strftime("%Y-%m-%d %I:%M %p")

    table = dbc.Table.from_dataframe(
        display_df.head(24),
        striped=True,
        bordered=True,
        hover=True,
        responsive=True
    )

    return (
        best_score,
        best_time,
        top_three_text,
        current_recommendation,
        line_fig,
        temp_fig,
        factor_fig,
        table
    )

# =========================================================
# RUN APP
# =========================================================

if __name__ == "__main__":
    app.run(debug=True)
