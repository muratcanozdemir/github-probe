from __future__ import annotations

import signal

import pytest

from org_harvest.interrupt import InterruptGuard


class TestInterruptGuard:
    def test_not_requested_before_any_signal(self):
        guard = InterruptGuard()
        with guard:
            assert guard.requested is False

    def test_first_signal_sets_requested_without_raising_ac_4_11(self):
        guard = InterruptGuard()
        with guard:
            guard._handle_sigint(signal.SIGINT, None)
            assert guard.requested is True

    def test_second_signal_raises_keyboardinterrupt_immediately_ac_4_11(self):
        guard = InterruptGuard()
        with guard:
            guard._handle_sigint(signal.SIGINT, None)
            with pytest.raises(KeyboardInterrupt):
                guard._handle_sigint(signal.SIGINT, None)

    def test_previous_handler_is_restored_on_exit(self):
        original = signal.getsignal(signal.SIGINT)
        with InterruptGuard():
            assert signal.getsignal(signal.SIGINT) is not original
        assert signal.getsignal(signal.SIGINT) is original

    def test_previous_handler_is_restored_after_second_signal(self):
        original = signal.getsignal(signal.SIGINT)
        guard = InterruptGuard()
        with guard:
            guard._handle_sigint(signal.SIGINT, None)
            with pytest.raises(KeyboardInterrupt):
                guard._handle_sigint(signal.SIGINT, None)
        assert signal.getsignal(signal.SIGINT) is original

    def test_a_real_sigint_is_delivered_to_the_installed_handler(self):
        import os

        guard = InterruptGuard()
        with guard:
            os.kill(os.getpid(), signal.SIGINT)
            assert guard.requested is True
