import threading

from weave_enrichers.keylock import KeyedLocks


def test_lock_released_entry_removed():
    locks = KeyedLocks()
    with locks.hold("a"):
        assert len(locks) == 1
    assert len(locks) == 0


def test_waiter_keeps_the_same_lock():
    """A waiter shares the holder's lock, so dropping cache entries can never give a key two locks."""
    locks, inside, order = KeyedLocks(), threading.Event(), []
    release = threading.Event()

    def holder():
        with locks.hold("a"):
            inside.set()
            release.wait(5)
            order.append("holder")

    def waiter():
        inside.wait(5)
        with locks.hold("a"):
            order.append("waiter")

    t1, t2 = threading.Thread(target=holder), threading.Thread(target=waiter)
    t1.start(); t2.start()
    inside.wait(5)
    while len(locks) != 1 or locks._locks["a"][1] != 2:  # waiter registered on the same entry
        pass
    release.set()
    t1.join(5); t2.join(5)
    assert order == ["holder", "waiter"] and len(locks) == 0


def test_different_keys_do_not_block():
    locks = KeyedLocks()
    with locks.hold("a"):
        with locks.hold("b"):
            assert len(locks) == 2
