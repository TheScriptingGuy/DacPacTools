from datetime import UTC, datetime

from airflow import DAG
from airflow.providers.microsoft.mssql.operators.mssql import MsSqlOperator

_where = "x = 1"

with DAG("dag_dynamic", start_date=datetime(2025, 1, 1, tzinfo=UTC)) as dag:
    dyn = MsSqlOperator(
        task_id="dynamic_sql",
        mssql_conn_id="mssql_default",
        sql=f"SELECT * FROM dbo.T WHERE {_where}",
    )
