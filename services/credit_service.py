"""
Credit Management Service
"""
from sqlalchemy.orm import Session
from typing import Dict, Any
from datetime import datetime
import uuid

from services.database import UserCredit, CreditTransaction, TransactionType
from utils.logger import get_logger

logger = get_logger(__name__)


class CreditService:
    """Service for managing user credits"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def get_or_create_user_credits(
        self,
        app_name: str,
        user_id: str,
        lock: bool = False
    ) -> UserCredit:
        """
        Get or create user credits record.

        Args:
            app_name: App identifier.
            user_id: User identifier.
            lock: If True, acquire a row-level lock (SELECT ... FOR UPDATE).
                Use from any code path that will mutate the row to prevent
                lost-update races between concurrent FastAPI requests.

        Returns:
            The UserCredit row.
        """
        query = self.db.query(UserCredit).filter(
            UserCredit.app_name == app_name,
            UserCredit.user_id == user_id
        )
        if lock:
            query = query.with_for_update()
        user_credits = query.first()

        if not user_credits:
            user_credits = UserCredit(
                app_name=app_name,
                user_id=user_id,
                credit_balance=0
            )
            self.db.add(user_credits)
            self.db.commit()
            self.db.refresh(user_credits)

        return user_credits
    
    def add_credits(
        self,
        app_name: str,
        user_id: str,
        amount: int,
        payment_id: str,
        description: str = "Purchase"
    ) -> Dict[str, Any]:
        """
        Add credits to user account
        
        Returns:
            {
                "transaction_id": str,
                "balance_before": int,
                "balance_after": int,
                "amount": int
            }
        """
        # Lock the credit row for the duration of this transaction so concurrent
        # add/deduct/refund calls serialize at the DB instead of racing.
        user_credits = self.get_or_create_user_credits(app_name, user_id, lock=True)

        balance_before = user_credits.credit_balance
        balance_after = balance_before + amount
        
        # Update user credits
        user_credits.credit_balance = balance_after
        user_credits.total_credits_purchased += amount
        user_credits.last_purchase_at = datetime.utcnow()
        
        # Create transaction record
        transaction = CreditTransaction(
            transaction_id=f"tx_{uuid.uuid4().hex[:16]}",
            app_name=app_name,
            user_id=user_id,
            type=TransactionType.PURCHASE,
            amount=amount,
            balance_before=balance_before,
            balance_after=balance_after,
            payment_id=payment_id,
            description=description
        )
        
        self.db.add(transaction)
        self.db.commit()
        self.db.refresh(user_credits)
        
        logger.info(
            f"Credits added: {app_name}/{user_id}",
            extra={
                'extra_fields': {
                    'app_name': app_name,
                    'user_id': user_id,
                    'amount': amount,
                    'balance_before': balance_before,
                    'balance_after': balance_after,
                    'payment_id': payment_id,
                    'transaction_id': transaction.transaction_id
                }
            }
        )
        
        return {
            "transaction_id": transaction.transaction_id,
            "balance_before": balance_before,
            "balance_after": balance_after,
            "amount": amount
        }
    
    def deduct_credits(
        self,
        app_name: str,
        user_id: str,
        amount: int,
        description: str = "Usage"
    ) -> Dict[str, Any]:
        """
        Deduct credits from user account
        
        Raises:
            ValueError: If insufficient credits

        Returns:
            Transaction details
        """
        # Lock the credit row so concurrent deduct calls serialize at the DB.
        user_credits = self.get_or_create_user_credits(app_name, user_id, lock=True)

        if user_credits.credit_balance < amount:
            logger.warning(
                f"Insufficient credits: {app_name}/{user_id}",
                extra={
                    'extra_fields': {
                        'app_name': app_name,
                        'user_id': user_id,
                        'available': user_credits.credit_balance,
                        'required': amount
                    }
                }
            )
            raise ValueError(f"Insufficient credits. Available: {user_credits.credit_balance}, Required: {amount}")
        
        balance_before = user_credits.credit_balance
        balance_after = balance_before - amount
        
        # Update user credits
        user_credits.credit_balance = balance_after
        user_credits.total_credits_used += amount
        
        # Create transaction record
        transaction = CreditTransaction(
            transaction_id=f"tx_{uuid.uuid4().hex[:16]}",
            app_name=app_name,
            user_id=user_id,
            type=TransactionType.DEDUCTION,
            amount=-amount,  # Negative for deduction
            balance_before=balance_before,
            balance_after=balance_after,
            description=description
        )
        
        self.db.add(transaction)
        self.db.commit()
        
        logger.info(
            f"Credits deducted: {app_name}/{user_id}",
            extra={
                'extra_fields': {
                    'app_name': app_name,
                    'user_id': user_id,
                    'amount': amount,
                    'balance_before': balance_before,
                    'balance_after': balance_after,
                    'transaction_id': transaction.transaction_id
                }
            }
        )
        
        return {
            "transaction_id": transaction.transaction_id,
            "balance_before": balance_before,
            "balance_after": balance_after,
            "amount": -amount
        }
    
    def refund_credits(
        self,
        app_name: str,
        user_id: str,
        amount: int,
        payment_id: str,
        description: str = "Refund"
    ) -> Dict[str, Any]:
        """Refund credits (remove from balance)"""
        # Lock the credit row so concurrent refund/deduct calls serialize at the DB.
        user_credits = self.get_or_create_user_credits(app_name, user_id, lock=True)

        balance_before = user_credits.credit_balance
        balance_after = max(0, balance_before - amount)  # Don't go negative
        
        # Update user credits
        user_credits.credit_balance = balance_after
        
        # Create transaction record
        transaction = CreditTransaction(
            transaction_id=f"tx_{uuid.uuid4().hex[:16]}",
            app_name=app_name,
            user_id=user_id,
            type=TransactionType.REFUND,
            amount=-amount,
            balance_before=balance_before,
            balance_after=balance_after,
            payment_id=payment_id,
            description=description
        )
        
        self.db.add(transaction)
        self.db.commit()
        
        return {
            "transaction_id": transaction.transaction_id,
            "balance_before": balance_before,
            "balance_after": balance_after,
            "amount": -amount
        }
    
    def get_balance(self, app_name: str, user_id: str) -> int:
        """Get user's current credit balance"""
        user_credits = self.get_or_create_user_credits(app_name, user_id)
        return user_credits.credit_balance
    
    def get_transaction_history(
        self,
        app_name: str,
        user_id: str,
        limit: int = 20
    ) -> list:
        """Get user's transaction history"""
        transactions = self.db.query(CreditTransaction).filter(
            CreditTransaction.app_name == app_name,
            CreditTransaction.user_id == user_id
        ).order_by(CreditTransaction.created_at.desc()).limit(limit).all()
        
        return transactions

