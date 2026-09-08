# Spend Manager Service

A backend service for managing personal spending with CRUD operations on expenses and income, along with report generation and analysis capabilities.

## Features

- **Expense Management**: Create, read, update, and delete expense records
- **Income Management**: Create, read, update, and delete income records  
- **Data Persistence**: In-memory storage (can be extended to use databases)
- **Reporting**: Generate spending reports with category breakdowns
- **Filtering**: Filter expenses and income by date ranges, categories, and sources

## Architecture

The service is built with:

- **Pydantic models** for data validation and serialization
- **In-memory storage** for demonstration purposes (can be extended to use databases)
- **FastAPI** for potential web API integration (optional)
- **SQLAlchemy** for database support (optional)

## Installation

```bash
pip install -e .
```

## Usage

### Basic Usage

```python
from spend_manager_service import SpendManagerService, Expense, Income

# Create service instance
service = SpendManagerService()

# Create an expense
expense = Expense(
    amount=25.50,
    currency="USD",
    category="Food",
    description="Grocery shopping",
    date=datetime(2025, 10, 15),
    payee="Supermarket"
)

# Create the expense in the system
created_expense = service.create_expense(expense)

# List all expenses
expenses = service.list_expenses()

# Generate a report
report = service.generate_report(
    start_date=datetime(2025, 10, 1),
    end_date=datetime(2025, 10, 31)
)
```

### Command Line Usage

```bash
python main.py
```

This will demonstrate basic usage of the service with sample data.

## Models

### Expense
- `id`: Unique identifier (auto-generated)
- `amount`: Transaction amount (positive value)
- `currency`: Currency code (default: USD)
- `category`: Expense category
- `description`: Optional description
- `date`: Transaction date
- `payee`: Entity paying for the expense
- `tags`: Optional list of tags

### Income
- `id`: Unique identifier (auto-generated)
- `amount`: Transaction amount (positive value)
- `currency`: Currency code (default: USD)
- `source`: Income source (e.g., "Salary", "Freelance")
- `description`: Optional description
- `date`: Transaction date
- `tags`: Optional list of tags

## API Endpoints (Planned)

The service can be extended to provide REST API endpoints:

- `POST /expenses` - Create new expense
- `GET /expenses/{id}` - Get specific expense
- `PUT /expenses/{id}` - Update expense
- `DELETE /expenses/{id}` - Delete expense
- `GET /expenses` - List expenses with filtering
- `POST /income` - Create new income record
- `GET /reports` - Generate spending reports

## Future Enhancements

1. **Database Integration**: Add support for SQLite, PostgreSQL, or other databases
2. **Web API**: Implement FastAPI endpoints for RESTful access
3. **Advanced Reporting**: Add more sophisticated report generation with charts and visualizations
4. **Data Import**: Enhanced MoneyWell data import capabilities with better error handling
5. **User Management**: Add multi-user support with authentication
6. **Category Management**: Allow custom category creation and management

## Development

To run tests:

```bash
pip install -e .[dev]
pytest
```

## License

MIT License