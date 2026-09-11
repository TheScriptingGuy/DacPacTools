from datetime import UTC, datetime

from airflow import DAG
from airflow.providers.microsoft.mssql.operators.mssql import MsSqlOperator

with DAG("dag_simple", start_date=datetime(2025, 1, 1, tzinfo=UTC), catchup=False) as dag:
    t1 = MsSqlOperator(
        task_id="load_staging",
        mssql_conn_id="mssql_default",
        sql="INSERT INTO dbo.Stage SELECT * FROM dbo.Source",
    )
    t2 = MsSqlOperator(
        task_id="load_fact",
        mssql_conn_id="mssql_default",
        sql="EXEC dbo.usp_LoadFact",
    )
    t1 >> t2
