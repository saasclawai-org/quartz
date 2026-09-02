"""
Faucet v2 rate limiter tests (2026-09-02).

The faucet is the first onboarding path ("mine" is the second). Its drip
must be limited — 1 per address / 24h, 5 per source IP / hour — and quota
must only burn on SUCCESSFUL drips (allow() is check-only; commit() records).
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from testnet import FaucetRateLimiter


class TestFaucetRateLimiter:
    def test_first_drip_allowed(self):
        fl = FaucetRateLimiter()
        ok, wait, scope = fl.allow('QzAddrA', '1.2.3.4')
        assert ok and wait == 0 and scope == ''

    def test_same_address_blocked_for_24h(self):
        fl = FaucetRateLimiter()
        fl.commit('QzAddrA', '1.2.3.4')
        ok, wait, scope = fl.allow('QzAddrA', '9.9.9.9')  # different IP, same addr
        assert not ok
        assert '24h' in scope
        assert 86000 < wait <= 86400

    def test_per_ip_cap_of_five_per_hour(self):
        fl = FaucetRateLimiter()
        for i in range(5):                    # 5 drips from one IP (distinct addrs)
            fl.commit(f'QzAddr{i}', '1.2.3.4')
        ok, wait, scope = fl.allow('QzFreshAddr', '1.2.3.4')
        assert not ok
        assert 'IP' in scope

    def test_allow_is_check_only_and_does_not_burn_quota(self):
        fl = FaucetRateLimiter()
        fl.allow('QzAddrA', '1.2.3.4')        # no commit — transient failure path
        ok, _, _ = fl.allow('QzAddrA', '1.2.3.4')
        assert ok, 'failed drips must never burn quota'

    def test_stale_ip_entries_expire_after_one_hour(self):
        fl = FaucetRateLimiter()
        old = time.time() - 3700              # 5 drips just past the window
        fl._by_ip['1.2.3.4'] = [old] * 5
        ok, _, _ = fl.allow('QzFreshAddr', '1.2.3.4')
        assert ok

    def test_addresses_and_ips_track_independently(self):
        fl = FaucetRateLimiter()
        fl.commit('QzAddrA', '1.2.3.4')
        ok, _, _ = fl.allow('QzAddrB', '1.2.3.4')   # same IP, new addr: fine
        assert ok
        ok, _, _ = fl.allow('QzAddrA', '5.6.7.8')   # same addr, new IP: blocked
        assert not ok
