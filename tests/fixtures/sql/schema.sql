CREATE TABLE dbo.Customers (
    customer_id INT NOT NULL PRIMARY KEY,
    name        NVARCHAR(200) NOT NULL,
    region      NVARCHAR(50) NULL
);

CREATE TABLE dbo.Orders (
    order_id     INT NOT NULL PRIMARY KEY,
    customer_id  INT NOT NULL,
    order_date   DATE NOT NULL,
    amount       DECIMAL(18,2) NOT NULL
);

CREATE VIEW dbo.v_CustomerOrders AS
SELECT c.customer_id, c.name AS customer_name, c.region,
       o.order_id, o.order_date, o.amount
FROM dbo.Customers c
JOIN dbo.Orders o ON o.customer_id = c.customer_id;

CREATE PROCEDURE dbo.usp_TopSpenders AS
WITH totals AS (
    SELECT customer_id, SUM(amount) AS total_amount
    FROM dbo.v_CustomerOrders
    GROUP BY customer_id
)
SELECT customer_id, total_amount FROM totals;
