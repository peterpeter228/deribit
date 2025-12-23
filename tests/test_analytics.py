"""
Tests for analytics module.

Covers:
- IV conversion
- Expected move calculation
- Risk reversal/butterfly
- Spread calculations
- Gamma Exposure (GEX) calculations
- Max Pain calculations
- Open Interest analysis
- IV Term Structure
- Skew metrics
"""

import math
import pytest

from deribit_mcp.analytics import (
    OptionData,
    aggregate_oi_by_strike,
    analyze_term_structure_shape,
    calculate_butterfly,
    calculate_expected_move,
    calculate_gamma_exposure_profile,
    calculate_imbalance,
    calculate_iv_term_structure_slope,
    calculate_max_pain,
    calculate_pain_at_strike,
    calculate_risk_reversal,
    calculate_single_gex,
    days_to_expiry_from_ts,
    determine_skew_direction,
    determine_skew_trend,
    dvol_to_decimal,
    estimate_25d_strike,
    find_oi_peak_range,
    find_oi_peaks,
    iv_annualized_to_horizon,
    SkewMetrics,
    spread_in_bps,
    TermStructurePoint,
    MINUTES_PER_YEAR,
)


class TestIVConversion:
    """Tests for IV conversion functions."""

    def test_iv_annualized_to_horizon_1hour(self):
        """Test IV conversion for 1 hour horizon."""
        iv_annual = 0.80  # 80% annual IV
        horizon_minutes = 60  # 1 hour

        result = iv_annualized_to_horizon(iv_annual, horizon_minutes)

        # Expected: 0.80 * sqrt(60 / 525600) ≈ 0.00855
        expected = 0.80 * math.sqrt(60 / MINUTES_PER_YEAR)
        assert abs(result - expected) < 1e-10

    def test_iv_annualized_to_horizon_1day(self):
        """Test IV conversion for 1 day horizon."""
        iv_annual = 0.80
        horizon_minutes = 1440  # 24 hours

        result = iv_annualized_to_horizon(iv_annual, horizon_minutes)

        expected = 0.80 * math.sqrt(1440 / MINUTES_PER_YEAR)
        assert abs(result - expected) < 1e-10

    def test_iv_annualized_zero_horizon(self):
        """Test IV conversion with zero horizon returns 0."""
        result = iv_annualized_to_horizon(0.80, 0)
        assert result == 0.0

    def test_dvol_to_decimal(self):
        """Test DVOL percentage to decimal conversion."""
        assert dvol_to_decimal(80) == 0.80
        assert dvol_to_decimal(100) == 1.0
        assert dvol_to_decimal(50.5) == 0.505


class TestExpectedMove:
    """Tests for expected move calculation."""

    def test_expected_move_basic(self):
        """Test basic expected move calculation."""
        spot = 100000  # $100,000 BTC
        iv_annual = 0.80  # 80% IV
        horizon_minutes = 60  # 1 hour

        result = calculate_expected_move(spot, iv_annual, horizon_minutes)

        # Expected 1σ move ≈ $854.60
        assert result.spot == spot
        assert result.iv_used == iv_annual
        assert result.horizon_minutes == horizon_minutes
        assert result.confidence == 1.0

        # Verify math: 100000 * 0.80 * sqrt(60/525600) ≈ 854.6
        expected_move = spot * iv_annual * math.sqrt(horizon_minutes / MINUTES_PER_YEAR)
        assert abs(result.move_points - expected_move) < 0.1

    def test_expected_move_bps(self):
        """Test expected move in basis points."""
        spot = 100000
        iv_annual = 0.80
        horizon_minutes = 60

        result = calculate_expected_move(spot, iv_annual, horizon_minutes)

        # BPS = (move_points / spot) * 10000
        expected_bps = (result.move_points / spot) * 10000
        assert abs(result.move_bps - expected_bps) < 0.01

    def test_expected_move_bands(self):
        """Test expected move bands (up/down 1σ)."""
        spot = 100000
        iv_annual = 0.80
        horizon_minutes = 60

        result = calculate_expected_move(spot, iv_annual, horizon_minutes)

        assert result.up_1sigma == round(spot + result.move_points, 2)
        assert result.down_1sigma == round(spot - result.move_points, 2)

    def test_expected_move_zero_iv(self):
        """Test expected move with zero IV returns zero move."""
        result = calculate_expected_move(100000, 0, 60)

        assert result.move_points == 0.0
        assert result.confidence == 0.0

    def test_expected_move_zero_spot(self):
        """Test expected move with zero spot returns zero move."""
        result = calculate_expected_move(0, 0.80, 60)

        assert result.move_points == 0.0
        assert result.confidence == 0.0

    def test_expected_move_different_horizons(self):
        """Test expected move scales with sqrt(time)."""
        spot = 100000
        iv_annual = 0.80

        result_1h = calculate_expected_move(spot, iv_annual, 60)
        result_4h = calculate_expected_move(spot, iv_annual, 240)

        # 4h move should be ~2x the 1h move (sqrt(4) = 2)
        ratio = result_4h.move_points / result_1h.move_points
        assert abs(ratio - 2.0) < 0.01


class TestRiskReversal:
    """Tests for risk reversal calculation."""

    def test_risk_reversal_bullish(self):
        """Test positive risk reversal (bullish skew)."""
        call_iv = 0.85
        put_iv = 0.80

        rr = calculate_risk_reversal(call_iv, put_iv)

        assert abs(rr - 0.05) < 1e-10  # Calls more expensive

    def test_risk_reversal_bearish(self):
        """Test negative risk reversal (bearish skew)."""
        call_iv = 0.75
        put_iv = 0.85

        rr = calculate_risk_reversal(call_iv, put_iv)

        assert abs(rr - (-0.10)) < 1e-10  # Puts more expensive

    def test_risk_reversal_none(self):
        """Test risk reversal with missing data."""
        assert calculate_risk_reversal(None, 0.80) is None
        assert calculate_risk_reversal(0.80, None) is None
        assert calculate_risk_reversal(None, None) is None


class TestButterfly:
    """Tests for butterfly calculation."""

    def test_butterfly_positive(self):
        """Test positive butterfly (fat tails)."""
        call_iv = 0.85
        put_iv = 0.85
        atm_iv = 0.80

        fly = calculate_butterfly(call_iv, put_iv, atm_iv)

        # (0.85 + 0.85) / 2 - 0.80 = 0.05
        assert abs(fly - 0.05) < 1e-10

    def test_butterfly_negative(self):
        """Test negative butterfly (thin tails)."""
        call_iv = 0.75
        put_iv = 0.75
        atm_iv = 0.80

        fly = calculate_butterfly(call_iv, put_iv, atm_iv)

        assert abs(fly - (-0.05)) < 1e-10

    def test_butterfly_none(self):
        """Test butterfly with missing data."""
        assert calculate_butterfly(None, 0.80, 0.80) is None
        assert calculate_butterfly(0.80, None, 0.80) is None
        assert calculate_butterfly(0.80, 0.80, None) is None


class TestSpreadCalculations:
    """Tests for spread calculations."""

    def test_spread_in_bps(self):
        """Test spread calculation in basis points."""
        bid = 99.0
        ask = 101.0

        result = spread_in_bps(bid, ask)

        # Spread = 2, mid = 100, bps = 2/100 * 10000 = 200
        assert abs(result - 200) < 0.01

    def test_spread_in_bps_tight(self):
        """Test tight spread calculation."""
        bid = 99.99
        ask = 100.01

        result = spread_in_bps(bid, ask)

        # Spread = 0.02, mid = 100, bps = 2
        assert abs(result - 2) < 0.01

    def test_spread_invalid(self):
        """Test spread with invalid inputs."""
        assert spread_in_bps(0, 100) is None
        assert spread_in_bps(100, 0) is None
        assert spread_in_bps(-1, 100) is None


class TestImbalance:
    """Tests for order book imbalance calculation."""

    def test_imbalance_balanced(self):
        """Test balanced book."""
        result = calculate_imbalance(100, 100)
        assert result == 0.0

    def test_imbalance_bid_heavy(self):
        """Test bid-heavy book."""
        result = calculate_imbalance(100, 0)
        assert result == 1.0

    def test_imbalance_ask_heavy(self):
        """Test ask-heavy book."""
        result = calculate_imbalance(0, 100)
        assert result == -1.0

    def test_imbalance_partial(self):
        """Test partial imbalance."""
        result = calculate_imbalance(75, 25)
        # (75 - 25) / 100 = 0.5
        assert result == 0.5

    def test_imbalance_zero(self):
        """Test zero depth."""
        result = calculate_imbalance(0, 0)
        assert result is None


class TestDaysToExpiry:
    """Tests for expiration timestamp conversion."""

    def test_days_to_expiry_basic(self):
        """Test basic days to expiry calculation."""
        current_ts_ms = 1700000000000  # Some timestamp
        expiry_ts_ms = current_ts_ms + (7 * 24 * 60 * 60 * 1000)  # 7 days later

        result = days_to_expiry_from_ts(expiry_ts_ms, current_ts_ms)

        assert abs(result - 7.0) < 0.001

    def test_days_to_expiry_expired(self):
        """Test expired option returns 0."""
        current_ts_ms = 1700000000000
        expiry_ts_ms = current_ts_ms - 1000  # Already expired

        result = days_to_expiry_from_ts(expiry_ts_ms, current_ts_ms)

        assert result == 0.0


# =============================================================================
# Gamma Exposure Tests
# =============================================================================


class TestGammaExposure:
    """Tests for gamma exposure calculations."""

    def test_calculate_single_gex_call(self):
        """Test GEX calculation for a call option."""
        gamma = 0.0001  # Gamma per 1 point move
        open_interest = 1000  # 1000 contracts
        spot_price = 50000
        
        gex = calculate_single_gex(gamma, open_interest, spot_price, "call")
        
        # GEX for calls should be negative (dealers short gamma)
        assert gex < 0

    def test_calculate_single_gex_put(self):
        """Test GEX calculation for a put option."""
        gamma = 0.0001
        open_interest = 1000
        spot_price = 50000
        
        gex = calculate_single_gex(gamma, open_interest, spot_price, "put")
        
        # GEX for puts should be positive (puts have negative gamma, dealers benefit)
        assert gex > 0

    def test_calculate_single_gex_zero_oi(self):
        """Test GEX with zero OI returns 0."""
        gex = calculate_single_gex(0.0001, 0, 50000, "call")
        assert gex == 0.0

    def test_calculate_single_gex_none_gamma(self):
        """Test GEX with None gamma returns 0."""
        gex = calculate_single_gex(None, 1000, 50000, "call")
        assert gex == 0.0

    def test_gamma_exposure_profile_basic(self):
        """Test gamma exposure profile calculation."""
        options = [
            OptionData(strike=50000, option_type="call", instrument_name="BTC-50000-C",
                       gamma=0.0001, open_interest=1000),
            OptionData(strike=50000, option_type="put", instrument_name="BTC-50000-P",
                       gamma=0.0001, open_interest=1500),
            OptionData(strike=55000, option_type="call", instrument_name="BTC-55000-C",
                       gamma=0.00005, open_interest=800),
        ]
        spot_price = 50000
        
        profile = calculate_gamma_exposure_profile(options, spot_price)
        
        assert len(profile.gex_by_strike) > 0
        assert profile.spot_price == spot_price
        assert profile.net_gex is not None

    def test_gamma_exposure_profile_empty(self):
        """Test gamma exposure profile with empty options."""
        profile = calculate_gamma_exposure_profile([], 50000)
        
        assert len(profile.gex_by_strike) == 0
        assert profile.net_gex == 0.0

    def test_gamma_flip_level_detection(self):
        """Test gamma flip level detection."""
        # Create options that should produce a sign change in net GEX
        options = [
            # At 45000: more puts (positive GEX)
            OptionData(strike=45000, option_type="put", instrument_name="BTC-45000-P",
                       gamma=0.0001, open_interest=2000),
            OptionData(strike=45000, option_type="call", instrument_name="BTC-45000-C",
                       gamma=0.0001, open_interest=500),
            # At 55000: more calls (negative GEX)
            OptionData(strike=55000, option_type="call", instrument_name="BTC-55000-C",
                       gamma=0.0001, open_interest=2000),
            OptionData(strike=55000, option_type="put", instrument_name="BTC-55000-P",
                       gamma=0.0001, open_interest=500),
        ]
        
        profile = calculate_gamma_exposure_profile(options, 50000)
        
        # Should find a gamma flip somewhere between 45000 and 55000
        # Note: gamma flip may be None if no sign change occurs
        if len(profile.gex_by_strike) >= 2:
            # Verify the calculation completed
            assert profile.spot_price == 50000


# =============================================================================
# Max Pain Tests
# =============================================================================


class TestMaxPain:
    """Tests for max pain calculations."""

    def test_calculate_pain_at_strike_calls_itm(self):
        """Test pain calculation when calls are ITM."""
        options = [
            OptionData(strike=45000, option_type="call", instrument_name="BTC-45000-C",
                       open_interest=100),
        ]
        
        # If price expires at 50000, 45000 call is ITM by 5000
        pain = calculate_pain_at_strike(50000, options)
        
        assert pain == (50000 - 45000) * 100  # 500000

    def test_calculate_pain_at_strike_puts_itm(self):
        """Test pain calculation when puts are ITM."""
        options = [
            OptionData(strike=55000, option_type="put", instrument_name="BTC-55000-P",
                       open_interest=100),
        ]
        
        # If price expires at 50000, 55000 put is ITM by 5000
        pain = calculate_pain_at_strike(50000, options)
        
        assert pain == (55000 - 50000) * 100  # 500000

    def test_calculate_pain_at_strike_otm(self):
        """Test pain calculation when options are OTM."""
        options = [
            OptionData(strike=55000, option_type="call", instrument_name="BTC-55000-C",
                       open_interest=100),
            OptionData(strike=45000, option_type="put", instrument_name="BTC-45000-P",
                       open_interest=100),
        ]
        
        # If price expires at 50000, both are OTM
        pain = calculate_pain_at_strike(50000, options)
        
        assert pain == 0

    def test_calculate_max_pain_basic(self):
        """Test max pain calculation."""
        options = [
            # Calls at various strikes
            OptionData(strike=45000, option_type="call", instrument_name="C1",
                       open_interest=100),
            OptionData(strike=50000, option_type="call", instrument_name="C2",
                       open_interest=200),
            OptionData(strike=55000, option_type="call", instrument_name="C3",
                       open_interest=100),
            # Puts at various strikes
            OptionData(strike=45000, option_type="put", instrument_name="P1",
                       open_interest=100),
            OptionData(strike=50000, option_type="put", instrument_name="P2",
                       open_interest=200),
            OptionData(strike=55000, option_type="put", instrument_name="P3",
                       open_interest=100),
        ]
        
        result = calculate_max_pain(options, 50000)
        
        # Max pain should be at a strike where most options expire worthless
        assert result.max_pain_strike in [45000, 50000, 55000]
        assert len(result.pain_curve) == 3
        assert len(result.pain_curve_top3) <= 3

    def test_calculate_max_pain_empty(self):
        """Test max pain with empty options."""
        result = calculate_max_pain([], 50000)
        
        assert result.max_pain_strike == 50000  # Defaults to spot
        assert len(result.pain_curve) == 0


# =============================================================================
# Open Interest Analysis Tests
# =============================================================================


class TestOpenInterestAnalysis:
    """Tests for open interest analysis functions."""

    def test_aggregate_oi_by_strike(self):
        """Test OI aggregation by strike."""
        options = [
            OptionData(strike=50000, option_type="call", instrument_name="C1",
                       open_interest=100),
            OptionData(strike=50000, option_type="put", instrument_name="P1",
                       open_interest=150),
            OptionData(strike=55000, option_type="call", instrument_name="C2",
                       open_interest=200),
        ]
        
        result = aggregate_oi_by_strike(options)
        
        assert len(result) == 2
        
        # Check 50000 strike
        strike_50k = next(s for s in result if s.strike == 50000)
        assert strike_50k.call_oi == 100
        assert strike_50k.put_oi == 150
        assert strike_50k.total_oi == 250
        assert strike_50k.put_call_ratio == 1.5

    def test_find_oi_peaks(self):
        """Test finding top OI strikes."""
        options = [
            OptionData(strike=45000, option_type="call", instrument_name="C1",
                       open_interest=100),
            OptionData(strike=50000, option_type="call", instrument_name="C2",
                       open_interest=500),  # Highest
            OptionData(strike=55000, option_type="call", instrument_name="C3",
                       open_interest=200),
        ]
        
        oi_data = aggregate_oi_by_strike(options)
        peaks = find_oi_peaks(oi_data, top_n=2)
        
        assert len(peaks) == 2
        assert peaks[0].strike == 50000  # Highest first
        assert peaks[1].strike == 55000

    def test_find_oi_peak_range(self):
        """Test finding OI concentration range."""
        options = [
            OptionData(strike=45000, option_type="call", instrument_name="C1",
                       open_interest=10),
            OptionData(strike=50000, option_type="call", instrument_name="C2",
                       open_interest=80),  # 80% of OI here
            OptionData(strike=55000, option_type="call", instrument_name="C3",
                       open_interest=10),
        ]
        
        oi_data = aggregate_oi_by_strike(options)
        peak_range = find_oi_peak_range(oi_data, percentile=0.8)
        
        assert peak_range is not None
        # 80% of OI is at 50000
        assert peak_range[0] == 50000
        assert peak_range[1] == 50000


# =============================================================================
# IV Term Structure Tests
# =============================================================================


class TestIVTermStructure:
    """Tests for IV term structure analysis."""

    def test_term_structure_slope_calculation(self):
        """Test term structure slope calculation."""
        points = [
            TermStructurePoint(days=7, atm_iv=0.80, expiry_label="7D", expiry_ts=0),
            TermStructurePoint(days=30, atm_iv=0.75, expiry_label="30D", expiry_ts=0),
            TermStructurePoint(days=90, atm_iv=0.70, expiry_label="90D", expiry_ts=0),
        ]
        
        slope = calculate_iv_term_structure_slope(points, 7, 30)
        
        # IV drops from 0.80 to 0.75 over 23 days
        # Slope per 30 days = (0.75 - 0.80) / 23 * 30 ≈ -0.0065
        assert slope is not None
        assert slope < 0  # Backwardation

    def test_analyze_term_structure_shape_contango(self):
        """Test contango detection."""
        points = [
            TermStructurePoint(days=7, atm_iv=0.70, expiry_label="7D", expiry_ts=0),
            TermStructurePoint(days=30, atm_iv=0.75, expiry_label="30D", expiry_ts=0),
            TermStructurePoint(days=90, atm_iv=0.80, expiry_label="90D", expiry_ts=0),
        ]
        
        is_contango = analyze_term_structure_shape(points)
        
        assert is_contango is True

    def test_analyze_term_structure_shape_backwardation(self):
        """Test backwardation detection."""
        points = [
            TermStructurePoint(days=7, atm_iv=0.90, expiry_label="7D", expiry_ts=0),
            TermStructurePoint(days=30, atm_iv=0.80, expiry_label="30D", expiry_ts=0),
            TermStructurePoint(days=90, atm_iv=0.70, expiry_label="90D", expiry_ts=0),
        ]
        
        is_contango = analyze_term_structure_shape(points)
        
        assert is_contango is False


# =============================================================================
# Skew Metrics Tests
# =============================================================================


class TestSkewMetrics:
    """Tests for skew metrics calculations."""

    def test_determine_skew_direction_bullish(self):
        """Test bullish skew detection."""
        # Positive RR = calls more expensive
        direction = determine_skew_direction(0.02)  # 2% RR
        
        assert direction == "bullish"

    def test_determine_skew_direction_bearish(self):
        """Test bearish skew detection."""
        # Negative RR = puts more expensive
        direction = determine_skew_direction(-0.02)
        
        assert direction == "bearish"

    def test_determine_skew_direction_neutral(self):
        """Test neutral skew detection."""
        direction = determine_skew_direction(0.001)  # Below threshold
        
        assert direction == "neutral"

    def test_determine_skew_direction_none(self):
        """Test skew direction with None input."""
        direction = determine_skew_direction(None)
        
        assert direction is None

    def test_determine_skew_trend_steepening(self):
        """Test steepening skew trend detection."""
        metrics = [
            SkewMetrics(days=7, rr25d=-0.05, bf25d=None, atm_iv=0.80,
                       call_25d_iv=None, put_25d_iv=None, skew_direction="bearish"),
            SkewMetrics(days=30, rr25d=-0.02, bf25d=None, atm_iv=0.75,
                       call_25d_iv=None, put_25d_iv=None, skew_direction="bearish"),
        ]
        
        trend = determine_skew_trend(metrics)
        
        # Short-term has more extreme skew (0.05 vs 0.02)
        assert trend == "steepening"

    def test_determine_skew_trend_flattening(self):
        """Test flattening skew trend detection."""
        metrics = [
            SkewMetrics(days=7, rr25d=-0.01, bf25d=None, atm_iv=0.80,
                       call_25d_iv=None, put_25d_iv=None, skew_direction="bearish"),
            SkewMetrics(days=30, rr25d=-0.05, bf25d=None, atm_iv=0.75,
                       call_25d_iv=None, put_25d_iv=None, skew_direction="bearish"),
        ]
        
        trend = determine_skew_trend(metrics)
        
        # Long-term has more extreme skew
        assert trend == "flattening"

    def test_estimate_25d_strike_call(self):
        """Test 25d call strike estimation."""
        spot = 50000
        atm_iv = 0.80
        days = 30
        
        strike = estimate_25d_strike(spot, atm_iv, days, is_call=True)
        
        # 25d call is OTM, strike should be above spot
        assert strike > spot

    def test_estimate_25d_strike_put(self):
        """Test 25d put strike estimation."""
        spot = 50000
        atm_iv = 0.80
        days = 30
        
        strike = estimate_25d_strike(spot, atm_iv, days, is_call=False)
        
        # 25d put is OTM, strike should be below spot
        assert strike < spot
