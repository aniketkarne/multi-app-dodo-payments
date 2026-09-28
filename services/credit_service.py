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
        user_id: str
    ) -> UserCredit:
        """Get or create user credits record"""
        user_credits = self.db.query(UserCredit).filter(
            UserCredit.app_name == app_name,
            UserCredit.user_id == user_id
        ).first()
        
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
        user_credits = self.get_or_create_user_credits(app_name, user_id)
        
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
        user_credits = self.get_or_create_user_credits(app_name, user_id)
        
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
        """Refund credits (remove from balance).

        The applied amount is clamped so the user's balance never goes
        negative. The transaction row records the *applied* amount, so the
        audit-log invariant ``balance_before + amount == balance_after``
        always holds, even when the requested refund exceeded the balance.
        Callers that need the original requested value (for reporting)
        can read it from the ``requested_amount`` field in the result.
        """
        if amount <= 0:
            raise ValueError(f"Refund amount must be positive, got {amount}")

        user_credits = self.get_or_create_user_credits(app_name, user_id)

        balance_before = user_credits.credit_balance
        applied = min(amount, balance_before)  # Never refund more than held
        balance_after = balance_before - applied
        clamped = applied < amount

        # Update user credits
        user_credits.credit_balance = balance_after

        # Create transaction record
        transaction = CreditTransaction(
            transaction_id=f"tx_{uuid.uuid4().hex[:16]}",
            app_name=app_name,
            user_id=user_id,
            type=TransactionType.REFUND,
            amount=-applied,  # Signed: applied (not requested) so row reconciles
            balance_before=balance_before,
            balance_after=balance_after,
            payment_id=payment_id,
            description=description
        )

        self.db.add(transaction)
        self.db.commit()

        if clamped:
            logger.warning(
                f"Refund clamped: requested {amount}, applied {applied} "
                f"for {app_name}/{user_id}",
                extra={
                    'extra_fields': {
                        'app_name': app_name,
                        'user_id': user_id,
                        'requested_amount': amount,
                        'applied_amount': applied,
                        'balance_before': balance_before,
                        'balance_after': balance_after,
                        'payment_id': payment_id,
                        'transaction_id': transaction.transaction_id
                    }
                }
            )
        else:
            logger.info(
                f"Credits refunded: {app_name}/{user_id}",
                extra={
                    'extra_fields': {
                        'app_name': app_name,
                        'user_id': user_id,
                        'requested_amount': amount,
                        'applied_amount': applied,
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
            "amount": -applied,
            "requested_amount": amount,
            "applied_amount": applied,
            "clamped": clamped,
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

