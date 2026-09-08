"""
Main service implementation for spend manager.
Handles CRUD operations for expenses and income, 
and provides report generation capabilities.
"""

from typing import List, Optional
from datetime import datetime
import uuid
from .models.expense import Expense
from .models.income import Income

class SpendManagerService:
    """Main service class for managing expenses and income"""
    
    def __init__(self):
        self.expenses: List[Expense] = []
        self.income: List[Income] = []
        
    def create_expense(self, expense: Expense) -> Expense:
        """Create a new expense record"""
        # Generate ID if not provided
        if not expense.id:
            expense.id = str(uuid.uuid4())
        
        self.expenses.append(expense)
        return expense
    
    def get_expense(self, expense_id: str) -> Optional[Expense]:
        """Get an expense by ID"""
        for expense in self.expenses:
            if expense.id == expense_id:
                return expense
        return None
    
    def update_expense(self, expense_id: str, updated_data: Expense) -> Optional[Expense]:
        """Update an existing expense"""
        for i, expense in enumerate(self.expenses):
            if expense.id == expense_id:
                # Update fields that are provided
                for field_name, field_value in updated_data.dict(exclude_unset=True).items():
                    setattr(expense, field_name, field_value)
                return expense
        return None
    
    def delete_expense(self, expense_id: str) -> bool:
        """Delete an expense by ID"""
        for i, expense in enumerate(self.expenses):
            if expense.id == expense_id:
                del self.expenses[i]
                return True
        return False
    
    def list_expenses(self, 
                     category: Optional[str] = None,
                     start_date: Optional[datetime] = None,
                     end_date: Optional[datetime] = None) -> List[Expense]:
        """List expenses with optional filtering"""
        filtered_expenses = self.expenses
        
        if category:
            filtered_expenses = [e for e in filtered_expenses if e.category == category]
            
        if start_date:
            filtered_expenses = [e for e in filtered_expenses if e.date >= start_date]
            
        if end_date:
            filtered_expenses = [e for e in filtered_expenses if e.date <= end_date]
            
        return filtered_expenses
    
    def create_income(self, income: Income) -> Income:
        """Create a new income record"""
        # Generate ID if not provided
        if not income.id:
            income.id = str(uuid.uuid4())
        
        self.income.append(income)
        return income
    
    def get_income(self, income_id: str) -> Optional[Income]:
        """Get an income record by ID"""
        for income in self.income:
            if income.id == income_id:
                return income
        return None
    
    def update_income(self, income_id: str, updated_data: Income) -> Optional[Income]:
        """Update an existing income record"""
        for i, income in enumerate(self.income):
            if income.id == income_id:
                # Update fields that are provided
                for field_name, field_value in updated_data.dict(exclude_unset=True).items():
                    setattr(income, field_name, field_value)
                return income
        return None
    
    def delete_income(self, income_id: str) -> bool:
        """Delete an income record by ID"""
        for i, income in enumerate(self.income):
            if income.id == income_id:
                del self.income[i]
                return True
        return False
    
    def list_income(self,
                   source: Optional[str] = None,
                   start_date: Optional[datetime] = None,
                   end_date: Optional[datetime] = None) -> List[Income]:
        """List income records with optional filtering"""
        filtered_income = self.income
        
        if source:
            filtered_income = [i for i in filtered_income if i.source == source]
            
        if start_date:
            filtered_income = [i for i in filtered_income if i.date >= start_date]
            
        if end_date:
            filtered_income = [i for i in filtered_income if i.date <= end_date]
            
        return filtered_income
    
    def generate_report(self, 
                       start_date: datetime,
                       end_date: datetime) -> dict:
        """Generate a spending report for the specified period"""
        # Filter expenses and income by date range
        expenses_in_period = [e for e in self.expenses 
                            if start_date <= e.date <= end_date]
        
        income_in_period = [i for i in self.income 
                           if start_date <= i.date <= end_date]
        
        # Calculate totals
        total_expenses = sum(e.amount for e in expenses_in_period)
        total_income = sum(i.amount for i in income_in_period)
        
        # Group expenses by category
        expense_categories = {}
        for expense in expenses_in_period:
            if expense.category in expense_categories:
                expense_categories[expense.category] += expense.amount
            else:
                expense_categories[expense.category] = expense.amount
        
        # Group income by source
        income_sources = {}
        for income in income_in_period:
            if income.source in income_sources:
                income_sources[income.source] += income.amount
            else:
                income_sources[income.source] = income.amount
        
        return {
            "period": {
                "start_date": start_date,
                "end_date": end_date
            },
            "summary": {
                "total_expenses": total_expenses,
                "total_income": total_income,
                "net_change": total_income - total_expenses
            },
            "expenses_by_category": expense_categories,
            "income_by_source": income_sources,
            "expense_count": len(expenses_in_period),
            "income_count": len(income_in_period)
        }