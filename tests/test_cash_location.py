"""Cash accounts can record where the money is kept."""

import unittest

from budget_tracker.db import connect
from budget_tracker.models import Account, AccountKind
from budget_tracker.repository import Repository, RepositoryError


class TestCashLocation(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Repository(connect(":memory:"))
        self.addCleanup(self.repo.close)

    def test_cash_account_stores_its_location(self):
        wallet = self.repo.add_account(Account(
            "Wallet", AccountKind.CASH, 10_000, storage_location="Home safe"
        ))
        loaded = self.repo.get_account(wallet.id)
        self.assertEqual(loaded.storage_location, "Home safe")
        [balance] = [
            balance for balance in self.repo.account_balances() if balance.account.id == wallet.id
        ]
        self.assertEqual(balance.account.storage_location, "Home safe")

    def test_only_cash_accounts_keep_the_location(self):
        account = Account(
            "Checking", AccountKind.CHECKING, storage_location="Home safe"
        )
        self.assertIsNone(account.storage_location)

    def test_location_can_be_changed(self):
        wallet = self.repo.add_account(Account(
            "Wallet", AccountKind.CASH, 10_000, storage_location="Home safe"
        ))
        wallet.storage_location = "Safe deposit box"
        self.repo.update_account(wallet)
        self.assertEqual(
            self.repo.get_account(wallet.id).storage_location, "Safe deposit box"
        )

    def test_location_is_limited_to_sixty_characters(self):
        with self.assertRaisesRegex(RepositoryError, "60 characters"):
            self.repo.add_account(Account(
                "Wallet", AccountKind.CASH, 10_000, storage_location="x" * 61
            ))
