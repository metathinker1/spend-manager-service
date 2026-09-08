"""
Unit tests for the Spend Manager Service
"""

import pytest
from datetime import datetime
from spend_manager_service import SpendManagerService, Expense, Income

def test_create_expense():
    """Test creating an expense"""
    service = SpendManagerService()
    
    expense = Expense(
        amount=25.50,
        currency="USD",
        category="Food",
        description="Grocery shopping",
        date=datetime(2025, 10, 15),
        payee="Supermarket"
    )
    
    created_expense = service.create_expense(expense)
    assert created_expense.id is not None
    assert created_expense.amount == 25.50
    assert created_expense.category == "Food"

def test_create_income():
    """Test creating an income record"""
    service = SpendManagerService()
    
    income = Income(
        amount=1500.00,
        currency="USD",
        source="Salary",
        description="Monthly salary",
        date=datetime(2025, 10, 1)
    )
    
    created_income = service.create_income(income)
    assert created_income.id is not None
    assert created_income.amount == 1500.00
    assert created_income.source == "Salary"

def test_list_expenses():
    """Test listing expenses"""
    service = SpendManagerService()
    
    expense1 = Expense(
        amount=25.50,
        currency="USD",
        category="Food",
        description="Grocery shopping",
        date=datetime(2025, 10, 15),
        payee="Supermarket"
    )
    
    expense2 = Expense(
        amount=15.75,
        currency="USD",
        category="Transport",
        description="Gas",
        date=datetime(2025, 10, 16),
        payee="Gas Station"
    )
    
    service.create_expense(expense1)
    service.create_expense(expense2)
    
    expenses = service.list_expenses()
    assert len(expenses) == 2

def test_generate_report():
    """Test generating a spending report"""
    service = SpendManagerService()
    
    expense = Expense(
        amount=25.50,
        currency="USD",
        category="Food",
        description="Grocery shopping",
        date=datetime(2025, 10, 15),
        payee="Supermarket"
    )
    
    income = Income(
        amount=1500.00,
        currency="USD",
        source="Salary",
        description="Monthly salary",
        date=datetime(2025, 10, 1)
    )
    
    service.create_expense(expense)
    service.create_income(income)
    
    report = service.generate_report(
        start_date=datetime(2025, 10, 1),
        end_date=datetime(2025, 10, 31)
    )
    
    assert report["summary"]["total_expenses"] == 25.50
    assert report["summary"]["total_income"] == 1500.00
    assert report["expense_count"] == 1
    assert report["income_count"] == 1

def test_filter_expenses_by_category():
    """Test filtering expenses by category"""
    service = SpendManagerService()
    
    expense1 = Expense(
        amount=25.50,
        currency="USD",
        category="Food",
        description="Grocery shopping",
        date=datetime(2025, 10, 15),
        payee="Supermarket"
    )
    
    expense2 = Expense(
        amount=15.75,
        currency="USD",
        category="Transport",
        description="Gas",
        date=datetime(2025, 10, 16),
        payee="Gas Station"
    )
    
    service.create_expense(expense1)
    service.create_expense(expense2)
    
    expenses = service.list_expenses(category="Food")
    assert len(expenses) == 1
    assert expenses[0].category == "Food"

def test_update_expense():
    """Test updating an expense"""
    service = SpendManagerService()
    
    expense = Expense(
        amount=25.50,
        currency="USD",
        category="Food",
        description="Grocery shopping",
        date=datetime(2025, 10, 15),
        payee="Supermarket"
    )
    
    created_expense = service.create_expense(expense)
    
    # Update the expense
    updated_data = Expense(
        amount=30.00,
        category="Food",
        description="Grocery shopping - Updated"
    )
    
    updated_expense = service.update_expense(created_expense.id, updated_data)
    assert updated_expense.amount == 30.00
    assert updated_expense.description == "Grocery shopping - Updated"

def test_delete_expense():
    """Test deleting an expense"""
    service = SpendManagerService()
    
    expense = Expense(
        amount=25.50,
        currency="USD",
        category="Food",
        description="Grocery shopping",
        date=datetime(2025, 10, 15),
        payee="Supermarket"
    )
    
    created_expense = service.create_expense(expense)
    assert len(service.list_expenses()) == 1
    
    deleted = service.delete_expense(created_expense.id)
    assert deleted is True
    assert len(service.list_expenses()) == 0

def test_invalid_amount():
    """Test that invalid amounts raise validation errors"""
    with pytest.raises(Exception):
        Expense(
            amount=-10.00,  # Negative amount should fail
            currency="USD",
            category="Food",
            description="Grocery shopping",
            date=datetime(2025, 10, 15),
            payee="Supermarket"
        )