from datetime import UTC, datetime

from airflow import DAG
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator

with DAG("dag_template", start_date=datetime(2025, 1, 1, tzinfo=UTC), catchup=False) as dag:
    load = SQLExecuteQueryOperator(
        task_id="load_dim",
        conn_id="mssql_warehouse",
        sql="queries/load_dim.sql",
    )
