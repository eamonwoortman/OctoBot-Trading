#  Drakkar-Software OctoBot-Trading
#  Copyright (c) Drakkar-Software, All rights reserved.
#
#  This library is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3.0 of the License, or (at your option) any later version.
#
#  This library is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#  Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public
#  License along with this library.
import os

import pytest
import asyncio

from octobot_trading.enums import MarkPriceSources
from tests.exchanges import backtesting_exchange_manager, backtesting_config, fake_backtesting
from tests.data_manager import price_events_manager, prices_manager
from octobot_trading.data_manager.prices_manager import PricesManager, calculate_mark_price_from_recent_trade_prices
from tests import event_loop

# All test coroutines will be treated as marked.
from tests.util.random_numbers import random_price

pytestmark = pytest.mark.asyncio


async def test_initialize(prices_manager):
    assert prices_manager.mark_price == prices_manager.mark_price_set_time == 0
    assert not prices_manager.valid_price_received_event.is_set()

    # should be reset in init
    prices_manager.mark_price = 10
    prices_manager.mark_price_set_time = 10
    prices_manager.valid_price_received_event.set()

    await prices_manager.initialize()
    assert prices_manager.mark_price == prices_manager.mark_price_set_time == 0
    assert not prices_manager.valid_price_received_event.is_set()


async def test_set_mark_price(prices_manager):
    prices_manager.set_mark_price(10, MarkPriceSources.EXCHANGE_MARK_PRICE.value)
    assert prices_manager.mark_price == 10
    check_event_is_set(prices_manager)


async def test_set_mark_price_for_exchange_source(prices_manager):
    prices_manager.set_mark_price(10, MarkPriceSources.EXCHANGE_MARK_PRICE.value)
    assert prices_manager.mark_price == 10
    check_event_is_set(prices_manager)
    prices_manager.set_mark_price(25, MarkPriceSources.RECENT_TRADE_AVERAGE.value)
    assert prices_manager.mark_price == 10  # Drop first RT update
    prices_manager.set_mark_price(30, MarkPriceSources.RECENT_TRADE_AVERAGE.value)
    assert prices_manager.mark_price == 30
    prices_manager.set_mark_price(20, MarkPriceSources.TICKER_CLOSE_PRICE.value)
    assert prices_manager.mark_price == 30
    prices_manager.set_mark_price(15, MarkPriceSources.EXCHANGE_MARK_PRICE.value)
    assert prices_manager.mark_price == 15


async def test_set_mark_price_for_ticker_source_only(prices_manager):
    assert prices_manager.set_mark_price(10, MarkPriceSources.TICKER_CLOSE_PRICE.value) is True
    assert prices_manager.mark_price == 10
    check_event_is_set(prices_manager)


async def test_set_mark_price_for_rt_source_only(prices_manager):
    assert prices_manager.set_mark_price(10, MarkPriceSources.RECENT_TRADE_AVERAGE.value) is False
    assert prices_manager.mark_price == 0  # drop first RT mark price
    assert prices_manager.set_mark_price(25, MarkPriceSources.RECENT_TRADE_AVERAGE.value) is True
    assert prices_manager.mark_price == 25
    check_event_is_set(prices_manager)


async def test_set_mark_price_for_ticker_with_rt_source_outdated(prices_manager):
    prices_manager.set_mark_price(5, MarkPriceSources.RECENT_TRADE_AVERAGE.value)
    assert prices_manager.mark_price == 0  # Drop first RT update
    assert prices_manager.set_mark_price(10, MarkPriceSources.RECENT_TRADE_AVERAGE.value) is True
    assert prices_manager.mark_price == 10
    check_event_is_set(prices_manager)
    assert prices_manager.set_mark_price(40, MarkPriceSources.TICKER_CLOSE_PRICE.value) is False
    assert prices_manager.mark_price == 10  # should not be updated because RECENT_TRADE_AVERAGE has not expired
    check_event_is_set(prices_manager)
    assert prices_manager.set_mark_price(20, MarkPriceSources.TICKER_CLOSE_PRICE.value) is False
    assert prices_manager.mark_price == 10  # should not be updated because RECENT_TRADE_AVERAGE has not expired
    check_event_is_set(prices_manager)
    # force rt source expiration
    if not os.getenv('CYTHON_IGNORE'):
        prices_manager.mark_price_from_sources[MarkPriceSources.RECENT_TRADE_AVERAGE.value] = None
        assert prices_manager.set_mark_price(40, MarkPriceSources.TICKER_CLOSE_PRICE.value) is True
        assert prices_manager.mark_price == 40  # should be updated because RECENT_TRADE_AVERAGE has expired
        check_event_is_set(prices_manager)
        assert prices_manager.set_mark_price(8, MarkPriceSources.RECENT_TRADE_AVERAGE.value) is False
        assert prices_manager.mark_price == 40  # Drop first RT update after reset
        assert prices_manager.mark_price_from_sources[MarkPriceSources.RECENT_TRADE_AVERAGE.value][1] == 0
        assert prices_manager.set_mark_price(15, MarkPriceSources.RECENT_TRADE_AVERAGE.value) is True
        assert prices_manager.mark_price == 15
        assert prices_manager.mark_price_from_sources[MarkPriceSources.RECENT_TRADE_AVERAGE.value][1] != 0
        check_event_is_set(prices_manager)
        assert prices_manager.set_mark_price(20, MarkPriceSources.TICKER_CLOSE_PRICE.value) is False
        assert prices_manager.mark_price == 15  # should not be updated because RECENT_TRADE_AVERAGE has not expired
        check_event_is_set(prices_manager)

        # force rt source expiration
        prices_manager.mark_price_from_sources[MarkPriceSources.RECENT_TRADE_AVERAGE.value] = (
            prices_manager.mark_price_from_sources[MarkPriceSources.RECENT_TRADE_AVERAGE.value][0],
            prices_manager.mark_price_from_sources[MarkPriceSources.RECENT_TRADE_AVERAGE.value][1] -
            PricesManager.MARK_PRICE_VALIDITY
        )
        prices_manager.set_mark_price(40, MarkPriceSources.TICKER_CLOSE_PRICE.value)
        assert prices_manager.mark_price == 40  # should be updated because RECENT_TRADE_AVERAGE has expired
        check_event_is_set(prices_manager)


async def test_get_mark_price(prices_manager):
    # without a set price
    with pytest.raises(asyncio.TimeoutError):
        await prices_manager.get_mark_price(0.01)
    assert not prices_manager.valid_price_received_event.is_set()

    # set price
    prices_manager.set_mark_price(10, MarkPriceSources.EXCHANGE_MARK_PRICE.value)
    assert await prices_manager.get_mark_price(0.01) == 10
    assert prices_manager.valid_price_received_event.is_set()

    # expired price
    if not os.getenv('CYTHON_IGNORE'):
        prices_manager.exchange_manager.backtesting.time_manager.current_timestamp = 66666666
        with pytest.raises(asyncio.TimeoutError):
            await prices_manager.get_mark_price(0.01)
        assert not prices_manager.valid_price_received_event.is_set()

    # reset price with this time
    prices_manager.set_mark_price(10, MarkPriceSources.EXCHANGE_MARK_PRICE.value)
    assert await prices_manager.get_mark_price(0.01) == 10
    assert prices_manager.valid_price_received_event.is_set()

    # current time move within allowed range
    if not os.getenv('CYTHON_IGNORE'):
        prices_manager.exchange_manager.backtesting.time_manager.current_timestamp = 1
        assert await prices_manager.get_mark_price(0.01) == 10
        assert prices_manager.valid_price_received_event.is_set()

    # new value
    prices_manager.set_mark_price(42.0000172, MarkPriceSources.EXCHANGE_MARK_PRICE.value)
    assert await prices_manager.get_mark_price(0.01) == 42.0000172
    assert prices_manager.valid_price_received_event.is_set()

    # random value
    random_mark_price = random_price()
    prices_manager.set_mark_price(random_mark_price, MarkPriceSources.EXCHANGE_MARK_PRICE.value)
    assert await prices_manager.get_mark_price(0.01) == random_mark_price
    assert prices_manager.valid_price_received_event.is_set()


async def test_calculate_mark_price_from_recent_trade_prices():
    assert calculate_mark_price_from_recent_trade_prices([10, 5, 7]) == 7.333333333333333
    assert calculate_mark_price_from_recent_trade_prices([10, 20]) == 15
    assert calculate_mark_price_from_recent_trade_prices([]) == 0


def check_event_is_set(prices_manager):
    if not os.getenv('CYTHON_IGNORE'):
        assert prices_manager.mark_price_set_time == prices_manager.exchange_manager.exchange.get_exchange_current_time()
        assert prices_manager.valid_price_received_event.is_set()
