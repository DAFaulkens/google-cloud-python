# Copyright 2016 Google LLC All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


from functools import total_ordering
import unittest


class TestAbstractSessionPool(unittest.TestCase):

    def _getTargetClass(self):
        from google.cloud.spanner_v1.pool import AbstractSessionPool

        return AbstractSessionPool

    def _make_one(self, *args, **kwargs):
        return self._getTargetClass()(*args, **kwargs)

    def test_ctor_defaults(self):
        pool = self._make_one()
        self.assertIsNone(pool._database)

    def test_bind_abstract(self):
        pool = self._make_one()
        database = _Database('name')
        with self.assertRaises(NotImplementedError):
            pool.bind(database)

    def test_get_abstract(self):
        pool = self._make_one()
        with self.assertRaises(NotImplementedError):
            pool.get()

    def test_put_abstract(self):
        pool = self._make_one()
        session = object()
        with self.assertRaises(NotImplementedError):
            pool.put(session)

    def test_clear_abstract(self):
        pool = self._make_one()
        with self.assertRaises(NotImplementedError):
            pool.clear()

    def test_session_wo_kwargs(self):
        from google.cloud.spanner_v1.pool import SessionCheckout

        pool = self._make_one()
        checkout = pool.session()
        self.assertIsInstance(checkout, SessionCheckout)
        self.assertIs(checkout._pool, pool)
        self.assertIsNone(checkout._session)
        self.assertEqual(checkout._kwargs, {})

    def test_session_w_kwargs(self):
        from google.cloud.spanner_v1.pool import SessionCheckout

        pool = self._make_one()
        checkout = pool.session(foo='bar')
        self.assertIsInstance(checkout, SessionCheckout)
        self.assertIs(checkout._pool, pool)
        self.assertIsNone(checkout._session)
        self.assertEqual(checkout._kwargs, {'foo': 'bar'})


class TestFixedSizePool(unittest.TestCase):

    def _getTargetClass(self):
        from google.cloud.spanner_v1.pool import FixedSizePool

        return FixedSizePool

    def _make_one(self, *args, **kwargs):
        return self._getTargetClass()(*args, **kwargs)

    def test_ctor_defaults(self):
        pool = self._make_one()
        self.assertIsNone(pool._database)
        self.assertEqual(pool.size, 10)
        self.assertEqual(pool.default_timeout, 10)
        self.assertTrue(pool._sessions.empty())

    def test_ctor_explicit(self):
        pool = self._make_one(size=4, default_timeout=30)
        self.assertIsNone(pool._database)
        self.assertEqual(pool.size, 4)
        self.assertEqual(pool.default_timeout, 30)
        self.assertTrue(pool._sessions.empty())

    def test_bind(self):
        pool = self._make_one()
        database = _Database('name')
        SESSIONS = [_Session(database)] * 10
        database._sessions.extend(SESSIONS)

        pool.bind(database)

        self.assertIs(pool._database, database)
        self.assertEqual(pool.size, 10)
        self.assertEqual(pool.default_timeout, 10)
        self.assertTrue(pool._sessions.full())

        for session in SESSIONS:
            self.assertTrue(session._created)

    def test_get_non_expired(self):
        pool = self._make_one(size=4)
        database = _Database('name')
        SESSIONS = [_Session(database)] * 4
        database._sessions.extend(SESSIONS)
        pool.bind(database)

        session = pool.get()

        self.assertIs(session, SESSIONS[0])
        self.assertTrue(session._exists_checked)
        self.assertFalse(pool._sessions.full())

    def test_get_expired(self):
        pool = self._make_one(size=4)
        database = _Database('name')
        SESSIONS = [_Session(database)] * 5
        SESSIONS[0]._exists = False
        database._sessions.extend(SESSIONS)
        pool.bind(database)

        session = pool.get()

        self.assertIs(session, SESSIONS[4])
        self.assertTrue(session._created)
        self.assertTrue(SESSIONS[0]._exists_checked)
        self.assertFalse(pool._sessions.full())

    def test_get_empty_default_timeout(self):
        from six.moves.queue import Empty

        pool = self._make_one(size=1)
        queue = pool._sessions = _Queue()

        with self.assertRaises(Empty):
            pool.get()

        self.assertEqual(queue._got, {'block': True, 'timeout': 10})

    def test_get_empty_explicit_timeout(self):
        from six.moves.queue import Empty

        pool = self._make_one(size=1, default_timeout=0.1)
        queue = pool._sessions = _Queue()

        with self.assertRaises(Empty):
            pool.get(timeout=1)

        self.assertEqual(queue._got, {'block': True, 'timeout': 1})

    def test_put_full(self):
        from six.moves.queue import Full

        pool = self._make_one(size=4)
        database = _Database('name')
        SESSIONS = [_Session(database)] * 4
        database._sessions.extend(SESSIONS)
        pool.bind(database)

        with self.assertRaises(Full):
            pool.put(_Session(database))

        self.assertTrue(pool._sessions.full())

    def test_put_non_full(self):
        pool = self._make_one(size=4)
        database = _Database('name')
        SESSIONS = [_Session(database)] * 4
        database._sessions.extend(SESSIONS)
        pool.bind(database)
        pool._sessions.get()

        pool.put(_Session(database))

        self.assertTrue(pool._sessions.full())

    def test_clear(self):
        pool = self._make_one()
        database = _Database('name')
        SESSIONS = [_Session(database)] * 10
        database._sessions.extend(SESSIONS)
        pool.bind(database)
        self.assertTrue(pool._sessions.full())

        for session in SESSIONS:
            self.assertTrue(session._created)

        pool.clear()

        for session in SESSIONS:
            self.assertTrue(session._deleted)


class TestBurstyPool(unittest.TestCase):

    def _getTargetClass(self):
        from google.cloud.spanner_v1.pool import BurstyPool

        return BurstyPool

    def _make_one(self, *args, **kwargs):
        return self._getTargetClass()(*args, **kwargs)

    def test_ctor_defaults(self):
        pool = self._make_one()
        self.assertIsNone(pool._database)
        self.assertEqual(pool.target_size, 10)
        self.assertTrue(pool._sessions.empty())

    def test_ctor_explicit(self):
        pool = self._make_one(target_size=4)
        self.assertIsNone(pool._database)
        self.assertEqual(pool.target_size, 4)
        self.assertTrue(pool._sessions.empty())

    def test_get_empty(self):
        pool = self._make_one()
        database = _Database('name')
        database._sessions.append(_Session(database))
        pool.bind(database)

        session = pool.get()

        self.assertIsInstance(session, _Session)
        self.assertIs(session._database, database)
        self.assertTrue(session._created)
        self.assertTrue(pool._sessions.empty())

    def test_get_non_empty_session_exists(self):
        pool = self._make_one()
        database = _Database('name')
        previous = _Session(database)
        pool.bind(database)
        pool.put(previous)

        session = pool.get()

        self.assertIs(session, previous)
        self.assertFalse(session._created)
        self.assertTrue(session._exists_checked)
        self.assertTrue(pool._sessions.empty())

    def test_get_non_empty_session_expired(self):
        pool = self._make_one()
        database = _Database('name')
        previous = _Session(database, exists=False)
        newborn = _Session(database)
        database._sessions.append(newborn)
        pool.bind(database)
        pool.put(previous)

        session = pool.get()

        self.assertTrue(previous._exists_checked)
        self.assertIs(session, newborn)
        self.assertTrue(session._created)
        self.assertFalse(session._exists_checked)
        self.assertTrue(pool._sessions.empty())

    def test_put_empty(self):
        pool = self._make_one()
        database = _Database('name')
        pool.bind(database)
        session = _Session(database)

        pool.put(session)

        self.assertFalse(pool._sessions.empty())

    def test_put_full(self):
        pool = self._make_one(target_size=1)
        database = _Database('name')
        pool.bind(database)
        older = _Session(database)
        pool.put(older)
        self.assertFalse(pool._sessions.empty())

        younger = _Session(database)
        pool.put(younger)  # discarded silently

        self.assertTrue(younger._deleted)
        self.assertIs(pool.get(), older)

    def test_put_full_expired(self):
        pool = self._make_one(target_size=1)
        database = _Database('name')
        pool.bind(database)
        older = _Session(database)
        pool.put(older)
        self.assertFalse(pool._sessions.empty())

        younger = _Session(database, exists=False)
        pool.put(younger)  # discarded silently

        self.assertTrue(younger._deleted)
        self.assertIs(pool.get(), older)

    def test_clear(self):
        pool = self._make_one()
        database = _Database('name')
        pool.bind(database)
        previous = _Session(database)
        pool.put(previous)

        pool.clear()

        self.assertTrue(previous._deleted)


class TestPingingPool(unittest.TestCase):

    def _getTargetClass(self):
        from google.cloud.spanner_v1.pool import PingingPool

        return PingingPool

    def _make_one(self, *args, **kwargs):
        return self._getTargetClass()(*args, **kwargs)

    def test_ctor_defaults(self):
        pool = self._make_one()
        self.assertIsNone(pool._database)
        self.assertEqual(pool.size, 10)
        self.assertEqual(pool.default_timeout, 10)
        self.assertEqual(pool._delta.seconds, 3000)
        self.assertTrue(pool._sessions.empty())

    def test_ctor_explicit(self):
        pool = self._make_one(size=4, default_timeout=30, ping_interval=1800)
        self.assertIsNone(pool._database)
        self.assertEqual(pool.size, 4)
        self.assertEqual(pool.default_timeout, 30)
        self.assertEqual(pool._delta.seconds, 1800)
        self.assertTrue(pool._sessions.empty())

    def test_bind(self):
        pool = self._make_one()
        database = _Database('name')
        SESSIONS = [_Session(database)] * 10
        database._sessions.extend(SESSIONS)

        pool.bind(database)

        self.assertIs(pool._database, database)
        self.assertEqual(pool.size, 10)
        self.assertEqual(pool.default_timeout, 10)
        self.assertEqual(pool._delta.seconds, 3000)
        self.assertTrue(pool._sessions.full())

        for session in SESSIONS:
            self.assertTrue(session._created)

    def test_get_hit_no_ping(self):
        pool = self._make_one(size=4)
        database = _Database('name')
        SESSIONS = [_Session(database)] * 4
        database._sessions.extend(SESSIONS)
        pool.bind(database)

        session = pool.get()

        self.assertIs(session, SESSIONS[0])
        self.assertFalse(session._exists_checked)
        self.assertFalse(pool._sessions.full())

    def test_get_hit_w_ping(self):
        import datetime
        from google.cloud._testing import _Monkey
        from google.cloud.spanner_v1 import pool as MUT

        pool = self._make_one(size=4)
        database = _Database('name')
        SESSIONS = [_Session(database)] * 4
        database._sessions.extend(SESSIONS)

        sessions_created = (
            datetime.datetime.utcnow() - datetime.timedelta(seconds=4000))

        with _Monkey(MUT, _NOW=lambda: sessions_created):
            pool.bind(database)

        session = pool.get()

        self.assertIs(session, SESSIONS[0])
        self.assertTrue(session._exists_checked)
        self.assertFalse(pool._sessions.full())

    def test_get_hit_w_ping_expired(self):
        import datetime
        from google.cloud._testing import _Monkey
        from google.cloud.spanner_v1 import pool as MUT

        pool = self._make_one(size=4)
        database = _Database('name')
        SESSIONS = [_Session(database)] * 5
        SESSIONS[0]._exists = False
        database._sessions.extend(SESSIONS)

        sessions_created = (
            datetime.datetime.utcnow() - datetime.timedelta(seconds=4000))

        with _Monkey(MUT, _NOW=lambda: sessions_created):
            pool.bind(database)

        session = pool.get()

        self.assertIs(session, SESSIONS[4])
        self.assertTrue(session._created)
        self.assertTrue(SESSIONS[0]._exists_checked)
        self.assertFalse(pool._sessions.full())

    def test_get_empty_default_timeout(self):
        from six.moves.queue import Empty

        pool = self._make_one(size=1)
        queue = pool._sessions = _Queue()

        with self.assertRaises(Empty):
            pool.get()

        self.assertEqual(queue._got, {'block': True, 'timeout': 10})

    def test_get_empty_explicit_timeout(self):
        from six.moves.queue import Empty

        pool = self._make_one(size=1, default_timeout=0.1)
        queue = pool._sessions = _Queue()

        with self.assertRaises(Empty):
            pool.get(timeout=1)

        self.assertEqual(queue._got, {'block': True, 'timeout': 1})

    def test_put_full(self):
        from six.moves.queue import Full

        pool = self._make_one(size=4)
        database = _Database('name')
        SESSIONS = [_Session(database)] * 4
        database._sessions.extend(SESSIONS)
        pool.bind(database)

        with self.assertRaises(Full):
            pool.put(_Session(database))

        self.assertTrue(pool._sessions.full())

    def test_put_non_full(self):
        import datetime
        from google.cloud._testing import _Monkey
        from google.cloud.spanner_v1 import pool as MUT

        pool = self._make_one(size=1)
        queue = pool._sessions = _Queue()

        now = datetime.datetime.utcnow()
        database = _Database('name')
        session = _Session(database)

        with _Monkey(MUT, _NOW=lambda: now):
            pool.put(session)

        self.assertEqual(len(queue._items), 1)
        ping_after, queued = queue._items[0]
        self.assertEqual(ping_after, now + datetime.timedelta(seconds=3000))
        self.assertIs(queued, session)

    def test_clear(self):
        pool = self._make_one()
        database = _Database('name')
        SESSIONS = [_Session(database)] * 10
        database._sessions.extend(SESSIONS)
        pool.bind(database)
        self.assertTrue(pool._sessions.full())

        for session in SESSIONS:
            self.assertTrue(session._created)

        pool.clear()

        for session in SESSIONS:
            self.assertTrue(session._deleted)

    def test_ping_empty(self):
        pool = self._make_one(size=1)
        pool.ping()  # Does not raise 'Empty'

    def test_ping_oldest_fresh(self):
        pool = self._make_one(size=1)
        database = _Database('name')
        SESSIONS = [_Session(database)] * 1
        database._sessions.extend(SESSIONS)
        pool.bind(database)

        pool.ping()

        self.assertFalse(SESSIONS[0]._exists_checked)

    def test_ping_oldest_stale_but_exists(self):
        import datetime
        from google.cloud._testing import _Monkey
        from google.cloud.spanner_v1 import pool as MUT

        pool = self._make_one(size=1)
        database = _Database('name')
        SESSIONS = [_Session(database)] * 1
        database._sessions.extend(SESSIONS)
        pool.bind(database)

        later = datetime.datetime.utcnow() + datetime.timedelta(seconds=4000)
        with _Monkey(MUT, _NOW=lambda: later):
            pool.ping()

        self.assertTrue(SESSIONS[0]._exists_checked)

    def test_ping_oldest_stale_and_not_exists(self):
        import datetime
        from google.cloud._testing import _Monkey
        from google.cloud.spanner_v1 import pool as MUT

        pool = self._make_one(size=1)
        database = _Database('name')
        SESSIONS = [_Session(database)] * 2
        SESSIONS[0]._exists = False
        database._sessions.extend(SESSIONS)
        pool.bind(database)

        later = datetime.datetime.utcnow() + datetime.timedelta(seconds=4000)
        with _Monkey(MUT, _NOW=lambda: later):
            pool.ping()

        self.assertTrue(SESSIONS[0]._exists_checked)
        self.assertTrue(SESSIONS[1]._created)


class TestTransactionPingingPool(unittest.TestCase):

    def _getTargetClass(self):
        from google.cloud.spanner_v1.pool import TransactionPingingPool

        return TransactionPingingPool

    def _make_one(self, *args, **kwargs):
        return self._getTargetClass()(*args, **kwargs)

    def test_ctor_defaults(self):
        pool = self._make_one()
        self.assertIsNone(pool._database)
        self.assertEqual(pool.size, 10)
        self.assertEqual(pool.default_timeout, 10)
        self.assertEqual(pool._delta.seconds, 3000)
        self.assertTrue(pool._sessions.empty())
        self.assertTrue(pool._pending_sessions.empty())

    def test_ctor_explicit(self):
        pool = self._make_one(size=4, default_timeout=30, ping_interval=1800)
        self.assertIsNone(pool._database)
        self.assertEqual(pool.size, 4)
        self.assertEqual(pool.default_timeout, 30)
        self.assertEqual(pool._delta.seconds, 1800)
        self.assertTrue(pool._sessions.empty())
        self.assertTrue(pool._pending_sessions.empty())

    def test_bind(self):
        pool = self._make_one()
        database = _Database('name')
        SESSIONS = [_Session(database) for _ in range(10)]
        database._sessions.extend(SESSIONS)

        pool.bind(database)

        self.assertIs(pool._database, database)
        self.assertEqual(pool.size, 10)
        self.assertEqual(pool.default_timeout, 10)
        self.assertEqual(pool._delta.seconds, 3000)
        self.assertTrue(pool._sessions.full())

        for session in SESSIONS:
            self.assertTrue(session._created)
            txn = session._transaction
            self.assertTrue(txn._begun)

        self.assertTrue(pool._pending_sessions.empty())

    def test_bind_w_timestamp_race(self):
        import datetime
        from google.cloud._testing import _Monkey
        from google.cloud.spanner_v1 import pool as MUT
        NOW = datetime.datetime.utcnow()
        pool = self._make_one()
        database = _Database('name')
        SESSIONS = [_Session(database) for _ in range(10)]
        database._sessions.extend(SESSIONS)

        with _Monkey(MUT, _NOW=lambda: NOW):
            pool.bind(database)

        self.assertIs(pool._database, database)
        self.assertEqual(pool.size, 10)
        self.assertEqual(pool.default_timeout, 10)
        self.assertEqual(pool._delta.seconds, 3000)
        self.assertTrue(pool._sessions.full())

        for session in SESSIONS:
            self.assertTrue(session._created)
            txn = session._transaction
            self.assertTrue(txn._begun)

        self.assertTrue(pool._pending_sessions.empty())

    def test_put_full(self):
        from six.moves.queue import Full

        pool = self._make_one(size=4)
        database = _Database('name')
        SESSIONS = [_Session(database) for _ in range(4)]
        database._sessions.extend(SESSIONS)
        pool.bind(database)

        with self.assertRaises(Full):
            pool.put(_Session(database))

        self.assertTrue(pool._sessions.full())

    def test_put_non_full_w_active_txn(self):
        pool = self._make_one(size=1)
        queue = pool._sessions = _Queue()
        pending = pool._pending_sessions = _Queue()
        database = _Database('name')
        session = _Session(database)
        txn = session.transaction()

        pool.put(session)

        self.assertEqual(len(queue._items), 1)
        _, queued = queue._items[0]
        self.assertIs(queued, session)

        self.assertEqual(len(pending._items), 0)
        self.assertFalse(txn._begun)

    def test_put_non_full_w_committed_txn(self):
        pool = self._make_one(size=1)
        queue = pool._sessions = _Queue()
        pending = pool._pending_sessions = _Queue()
        database = _Database('name')
        session = _Session(database)
        committed = session.transaction()
        committed._committed = True

        pool.put(session)

        self.assertEqual(len(queue._items), 0)

        self.assertEqual(len(pending._items), 1)
        self.assertIs(pending._items[0], session)
        self.assertIsNot(session._transaction, committed)
        self.assertFalse(session._transaction._begun)

    def test_put_non_full(self):
        pool = self._make_one(size=1)
        queue = pool._sessions = _Queue()
        pending = pool._pending_sessions = _Queue()
        database = _Database('name')
        session = _Session(database)

        pool.put(session)

        self.assertEqual(len(queue._items), 0)
        self.assertEqual(len(pending._items), 1)
        self.assertIs(pending._items[0], session)

        self.assertFalse(pending.empty())

    def test_begin_pending_transactions_empty(self):
        pool = self._make_one(size=1)
        pool.begin_pending_transactions()  # no raise

    def test_begin_pending_transactions_non_empty(self):
        pool = self._make_one(size=1)
        pool._sessions = _Queue()

        database = _Database('name')
        TRANSACTIONS = [_Transaction()]
        PENDING_SESSIONS = [
            _Session(database, transaction=txn) for txn in TRANSACTIONS]

        pending = pool._pending_sessions = _Queue(*PENDING_SESSIONS)
        self.assertFalse(pending.empty())

        pool.begin_pending_transactions()  # no raise

        for txn in TRANSACTIONS:
            self.assertTrue(txn._begun)

        self.assertTrue(pending.empty())


class TestSessionCheckout(unittest.TestCase):

    def _getTargetClass(self):
        from google.cloud.spanner_v1.pool import SessionCheckout

        return SessionCheckout

    def _make_one(self, *args, **kwargs):
        return self._getTargetClass()(*args, **kwargs)

    def test_ctor_wo_kwargs(self):
        pool = _Pool()
        checkout = self._make_one(pool)
        self.assertIs(checkout._pool, pool)
        self.assertIsNone(checkout._session)
        self.assertEqual(checkout._kwargs, {})

    def test_ctor_w_kwargs(self):
        pool = _Pool()
        checkout = self._make_one(pool, foo='bar')
        self.assertIs(checkout._pool, pool)
        self.assertIsNone(checkout._session)
        self.assertEqual(checkout._kwargs, {'foo': 'bar'})

    def test_context_manager_wo_kwargs(self):
        session = object()
        pool = _Pool(session)
        checkout = self._make_one(pool)

        self.assertEqual(len(pool._items), 1)
        self.assertIs(pool._items[0], session)

        with checkout as borrowed:
            self.assertIs(borrowed, session)
            self.assertEqual(len(pool._items), 0)

        self.assertEqual(len(pool._items), 1)
        self.assertIs(pool._items[0], session)
        self.assertEqual(pool._got, {})

    def test_context_manager_w_kwargs(self):
        session = object()
        pool = _Pool(session)
        checkout = self._make_one(pool, foo='bar')

        self.assertEqual(len(pool._items), 1)
        self.assertIs(pool._items[0], session)

        with checkout as borrowed:
            self.assertIs(borrowed, session)
            self.assertEqual(len(pool._items), 0)

        self.assertEqual(len(pool._items), 1)
        self.assertIs(pool._items[0], session)
        self.assertEqual(pool._got, {'foo': 'bar'})


class _Transaction(object):

    _begun = False
    _committed = False
    _rolled_back = False

    def begin(self):
        self._begun = True

    def committed(self):
        return self._committed


@total_ordering
class _Session(object):

    _transaction = None

    def __init__(self, database, exists=True, transaction=None):
        self._database = database
        self._exists = exists
        self._exists_checked = False
        self._created = False
        self._deleted = False
        self._transaction = transaction

    def __lt__(self, other):
        return id(self) < id(other)

    def create(self):
        self._created = True

    def exists(self):
        self._exists_checked = True
        return self._exists

    def delete(self):
        from google.cloud.exceptions import NotFound

        self._deleted = True
        if not self._exists:
            raise NotFound("unknown session")

    def transaction(self):
        txn = self._transaction = _Transaction()
        return txn


class _Database(object):

    def __init__(self, name):
        self.name = name
        self._sessions = []

    def session(self):
        return self._sessions.pop()


class _Queue(object):

    _size = 1

    def __init__(self, *items):
        self._items = list(items)

    def empty(self):
        return len(self._items) == 0

    def full(self):
        return len(self._items) >= self._size

    def get(self, **kwargs):
        from six.moves.queue import Empty

        self._got = kwargs
        try:
            return self._items.pop()
        except IndexError:
            raise Empty()

    def put(self, item, **kwargs):
        self._put = kwargs
        self._items.append(item)

    def put_nowait(self, item, **kwargs):
        self._put_nowait = kwargs
        self._items.append(item)


class _Pool(_Queue):

    _database = None
