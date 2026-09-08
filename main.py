#!/usr/bin/env python3
"""
Main entry point for the Spend Manager Service.
This service handles personal spending management with CRUD operations
on expenses and income, as well as report generation and analysis.
"""

import argparse
from datetime import datetime
from spend_manager_service import SpendManagerService, Expense, Income

def main():
    """Main function demonstrating usage of the spend manager service"""
    
    # Create a new service instance
    service = SpendManagerService()
    
    # Example: Create some test data
    expense1 = Expense(
        amount=25.50,
        currency="USD",
        category="Food",
        description="Grocery shopping",
        date=datetime(2025, 10, 15),
        payee="Supermarket"
    )
    
    income1 = Income(
        amount=1500.00,
        currency="USD",
        source="Salary",
        description="Monthly salary",
        date=datetime(2025, 10, 1)
    )
    
    # Create records
    created_expense = service.create_expense(expense1)
    created_income = service.create_income(income1)
    
    print(f"Created expense with ID: {created_expense.id}")
    print(f"Created income with ID: {created_income.id}")
    
    # List expenses
    all_expenses = service.list_expenses()
    print(f"Total expenses: {len(all_expenses)}")
    
    # Generate report
    report = service.generate_report(
        start_date=datetime(2025, 10, 1),
        end_date=datetime(2025, 10, 31)
    )
    
    print("\n--- Spending Report ---")
    print(f"Total Expenses: ${report['summary']['total_expenses']:.2f}")
    print(f"Total Income: ${report['summary']['total_income']:.2f}")
    print(f"Net Change: ${report['summary']['net_change']:.2f}")
    
    if report['expenses_by_category']:
        print("\nExpenses by Category:")
        for category, amount in report['expenses_by_category'].items():
            print(f"  {category}: ${amount:.2f}")
    
    if report['income_by_source']:
        print("\nIncome by Source:")
        for source, amount in report['income_by_source'].items():
            print(f"  {source}: ${amount:.2f}")

if __name__ == "__main__":
    main()