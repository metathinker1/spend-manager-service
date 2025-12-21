
from typing import List
from pydantic import BaseModel, Field

class MoneyWellTransaction(BaseModel):
    # 'Payee','Memo','Date','Type','Reference','Amount','Currency'
    payee: str = Field(alias="payee")
    memo: str = Field(alias="memo")
    date: str = Field(alias="date")
    type: str = Field(alias="type")
    reference: str = Field(alias="reference")
    amount: str = Field(alias="amount")
    currency: str = Field(alias="currency")
    category: str = Field(alias="category")


class MoneyWellTransactionData:
    def __int__(self):
        pass

    def get(self, file_path: str) -> List:
        with open(file_path) as fp:
            is_first_line = True
            transactions_list = []
            raw_transactions = []
            # transactions = pd.DataFrame(columns=['Payee', 'Memo', 'Date', 'Type', 'Reference', 'Amount', 'Currency'])
            for line in fp:
                # logging.info(line)
                parts = line.strip().split('\t')  # re.split(r'\t+', line.rstrip('\t'))
                # logging.info(len(parts))
                if is_first_line:
                    '''Skip header'''
                    is_first_line = False
                else:
                    if len(parts[1]) + len(parts[2]) + len(parts[3]) + len(parts[4]) > 0:
                        raw_transactions.append(
                            {"payee": parts[0], "memo": parts[1], "date": parts[2], "type": parts[3],
                             "reference": parts[4], "amount": parts[5], "currency": parts[6]}
                        )
                        # parts[2] = datetime.strptime(parts[2], date_format)
                        # parts[5] = float(parts[5])
                        # transactions.loc[len(transactions) + 1] = parts
                    else:
                        for raw_transaction in raw_transactions:
                            raw_transaction['category'] = parts[0]
                        transactions = [MoneyWellTransaction(**raw_transaction) for raw_transaction in raw_transactions]
                        transactions_list.append(transactions)

                        raw_transactions = []
            # logging.info(all_transactions)
            return transactions_list


if __name__ == '__main__':
    file_path = "/Users/robertwood/Google Drive/My Drive/AppData/MoneyWell/RobBudget_202510_Details.txt"
    moneywell_data = MoneyWellTransactionData()
    transactions_list = moneywell_data.get(file_path)
    print('stop here')
