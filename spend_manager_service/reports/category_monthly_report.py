import pandas as pd

from spend_manager_service.models.moneywell_transaction import MoneyWellTransaction


class CategoryMonthlyReport:
    """Aggregates transaction amounts by category and month."""

    def __init__(self, transactions: list[MoneyWellTransaction]):
        self.df = self._build_dataframe(transactions)

    def _build_dataframe(self, transactions: list[MoneyWellTransaction]) -> pd.DataFrame:
        """Convert transactions to DataFrame with proper types."""
        records = [t.model_dump() for t in transactions]
        df = pd.DataFrame(records)

        df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
        df["date"] = pd.to_datetime(df["date"], format="%m/%d/%y", errors="coerce")
        df["month"] = df["date"].dt.to_period("M")

        return df

    def aggregate(self) -> pd.DataFrame:
        """Return amounts aggregated by category and month."""
        return (
            self.df
            .groupby(["category", "month"], as_index=False)["amount"]
            .sum()
            .sort_values(["month", "category"])
        )

    def pivot(self) -> pd.DataFrame:
        """Return pivot table with categories as rows, months as columns."""
        return self.df.pivot_table(
            values="amount",
            index="category",
            columns="month",
            aggfunc="sum",
            fill_value=0,
        )
