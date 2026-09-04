INSERT INTO dbo.DimCustomer (Id, Name)
SELECT c.CustomerId, c.CustomerName
FROM stage.Customer AS c;
