import sys

from spend_manager_service.models.moneywell_transaction import MoneyWellTransactionData
from spend_manager_service.reports.category_monthly_report import CategoryMonthlyReport


class SpendingReportService:
    """Orchestrates transaction import and report generation."""

    def __init__(self, file_path: str):
        self.file_path = file_path

    def generate_category_monthly_report(self) -> CategoryMonthlyReport:
        """Import transactions and return a CategoryMonthlyReport."""
        data = MoneyWellTransactionData()
        transactions = data.import_file(self.file_path)
        return CategoryMonthlyReport(transactions)


if __name__ == '__main__':
    file_path = "/Users/robertwood/Google Drive/My Drive/AppData/MoneyWell/Exports/20260214_FullExport.txt"
    service = SpendingReportService(file_path)
    report = service.generate_category_monthly_report()
    print(f'report.df: {len(report.df)=}, {report.df.columns=}, {report.df.head(5)}')
    aggregate_df = report.aggregate()
    print(f'aggregate_df: {len(aggregate_df)=}, {aggregate_df.columns=}, {aggregate_df.head(5)}')
    aggregate_df.to_csv('MonthlyAggregationReport.csv', index=False)
    # aggregate_transpose = aggregate_df.transpose()
    # print(f'pivot_df: {len(aggregate_transpose)=}, {aggregate_transpose.columns=}, {aggregate_transpose.head(5)}')
    # aggregate_transpose.to_csv('MonthlyBudgetReport.csv', index=False)

    pivot_df = aggregate_df.pivot(index="category", columns="month", values="amount").fillna(0)
    pivot_df.index.name = "category"
    print(f'pivot_df: {len(pivot_df)=}, {pivot_df.columns=}, {pivot_df.head(5)}')
    pivot_df.to_csv('MonthlyBudgetReport.csv')
    print('stop here')