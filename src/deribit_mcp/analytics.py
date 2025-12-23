"""
Analytics module for volatility and expected move calculations.

Contains formulas for:
- IV to expected move conversion
- Risk reversal and butterfly calculations
- Volatility surface interpolation
- Gamma Exposure (GEX) calculations
- Max Pain calculations
- IV Term Structure analysis
- Skew metrics (RR25d, BF25d)
"""

import math
from dataclasses import dataclass, field
from typing import Literal

# Constants
MINUTES_PER_YEAR = 525600  # 365.25 * 24 * 60
DAYS_PER_YEAR = 365.25
HOURS_PER_YEAR = 8766  # 365.25 * 24
STANDARD_CONTRACT_SIZE = 1.0  # BTC options are 1 BTC per contract


@dataclass
class ExpectedMoveResult:
    """Result of expected move calculation."""

    spot: float
    iv_used: float  # Annualized IV (0-1 scale, e.g., 0.80 = 80%)
    iv_source: str
    horizon_minutes: int
    move_points: float  # 1σ move in price points
    move_bps: float  # 1σ move in basis points
    up_1sigma: float  # Spot + 1σ
    down_1sigma: float  # Spot - 1σ
    confidence: float  # 0-1


def iv_annualized_to_horizon(
    iv_annualized: float,
    horizon_minutes: int,
) -> float:
    """
    Convert annualized IV to IV for a specific time horizon.

    Formula: IV_horizon = IV_annual * sqrt(T_years)
    where T_years = horizon_minutes / MINUTES_PER_YEAR

    Args:
        iv_annualized: Annualized IV (e.g., 0.80 for 80%)
        horizon_minutes: Time horizon in minutes

    Returns:
        IV scaled to the horizon (same units as input)
    """
    if horizon_minutes <= 0:
        return 0.0

    t_years = horizon_minutes / MINUTES_PER_YEAR
    return iv_annualized * math.sqrt(t_years)


def calculate_expected_move(
    spot: float,
    iv_annualized: float,
    horizon_minutes: int,
    iv_source: str = "unknown",
    confidence: float = 1.0,
) -> ExpectedMoveResult:
    """
    Calculate expected move (1σ) based on IV and time horizon.

    The expected move represents the 1 standard deviation range,
    meaning ~68.3% of price moves should fall within this range
    (assuming log-normal distribution).

    Formula:
        expected_move = spot * IV_annual * sqrt(T_years)

    Where:
        - IV_annual is annualized implied volatility (decimal form)
        - T_years = horizon_minutes / 525600

    Args:
        spot: Current spot/index price
        iv_annualized: Annualized IV (e.g., 0.80 for 80%)
        horizon_minutes: Time horizon in minutes
        iv_source: Source of IV data ('dvol', 'atm_iv', etc.)
        confidence: Confidence in the calculation (0-1)

    Returns:
        ExpectedMoveResult with all computed values
    """
    if spot <= 0 or iv_annualized <= 0 or horizon_minutes <= 0:
        return ExpectedMoveResult(
            spot=spot,
            iv_used=iv_annualized,
            iv_source=iv_source,
            horizon_minutes=horizon_minutes,
            move_points=0.0,
            move_bps=0.0,
            up_1sigma=spot,
            down_1sigma=spot,
            confidence=0.0,
        )

    # Calculate time in years
    t_years = horizon_minutes / MINUTES_PER_YEAR

    # Expected move (1σ)
    move_points = spot * iv_annualized * math.sqrt(t_years)
    move_bps = (move_points / spot) * 10000

    return ExpectedMoveResult(
        spot=spot,
        iv_used=iv_annualized,
        iv_source=iv_source,
        horizon_minutes=horizon_minutes,
        move_points=round(move_points, 2),
        move_bps=round(move_bps, 2),
        up_1sigma=round(spot + move_points, 2),
        down_1sigma=round(spot - move_points, 2),
        confidence=confidence,
    )


def days_to_expiry_from_ts(expiration_ts_ms: int, current_ts_ms: int) -> float:
    """Calculate days to expiry from timestamps (milliseconds)."""
    diff_ms = expiration_ts_ms - current_ts_ms
    if diff_ms <= 0:
        return 0.0
    return diff_ms / (1000 * 60 * 60 * 24)


def find_nearest_tenor_instruments(
    instruments: list[dict],
    target_tenor_days: int,
    current_ts_ms: int,
    tolerance_days: float = 5.0,
) -> list[dict]:
    """
    Find instruments closest to the target tenor.

    Args:
        instruments: List of instrument dicts with 'expiration_timestamp'
        target_tenor_days: Target days to expiration
        current_ts_ms: Current timestamp in ms
        tolerance_days: Maximum deviation from target tenor

    Returns:
        List of instruments within tolerance, sorted by distance to target
    """
    result = []
    for inst in instruments:
        exp_ts = inst.get("expiration_timestamp", 0)
        if exp_ts <= current_ts_ms:
            continue

        days = days_to_expiry_from_ts(exp_ts, current_ts_ms)
        distance = abs(days - target_tenor_days)

        if distance <= tolerance_days:
            result.append({**inst, "_days_to_expiry": days, "_distance": distance})

    result.sort(key=lambda x: x["_distance"])
    return result


def calculate_risk_reversal(
    call_iv: float | None,
    put_iv: float | None,
) -> float | None:
    """
    Calculate 25-delta risk reversal.

    Risk Reversal = Call_IV(25d) - Put_IV(25d)

    Positive RR = calls more expensive (bullish skew)
    Negative RR = puts more expensive (bearish skew)
    """
    if call_iv is None or put_iv is None:
        return None
    return call_iv - put_iv


def calculate_butterfly(
    call_iv: float | None,
    put_iv: float | None,
    atm_iv: float | None,
) -> float | None:
    """
    Calculate 25-delta butterfly.

    Butterfly = (Call_IV(25d) + Put_IV(25d)) / 2 - ATM_IV

    Positive butterfly = wings more expensive (fat tails pricing)
    """
    if call_iv is None or put_iv is None or atm_iv is None:
        return None
    wing_avg = (call_iv + put_iv) / 2
    return wing_avg - atm_iv


@dataclass
class OptionChainAnalysis:
    """Analysis of an option chain for a single expiry."""

    expiry_ts: int
    days_to_expiry: float
    atm_strike: float | None
    atm_iv: float | None
    call_25d_strike: float | None
    call_25d_iv: float | None
    put_25d_strike: float | None
    put_25d_iv: float | None
    risk_reversal: float | None
    butterfly: float | None
    forward_price: float | None
    num_options: int


def find_atm_option(
    options: list[dict],
    underlying_price: float,
    option_type: Literal["call", "put"] = "call",
) -> dict | None:
    """
    Find the ATM option closest to underlying price.

    Args:
        options: List of option instruments with 'strike' and ticker data
        underlying_price: Current underlying/index price
        option_type: 'call' or 'put'

    Returns:
        The option dict closest to ATM, or None
    """
    filtered = [o for o in options if o.get("option_type") == option_type and o.get("strike")]

    if not filtered:
        return None

    return min(filtered, key=lambda x: abs(x.get("strike", 0) - underlying_price))


def find_delta_option(
    options: list[dict],
    target_delta: float,
    option_type: Literal["call", "put"],
) -> dict | None:
    """
    Find option closest to target delta.

    Args:
        options: List of options with 'greeks' containing 'delta'
        target_delta: Target absolute delta (e.g., 0.25)
        option_type: 'call' or 'put'

    Returns:
        The option closest to target delta, or None
    """
    filtered = [
        o
        for o in options
        if o.get("option_type") == option_type
        and o.get("greeks")
        and o["greeks"].get("delta") is not None
    ]

    if not filtered:
        return None

    # For puts, delta is negative, so we compare absolute values
    return min(filtered, key=lambda x: abs(abs(x["greeks"]["delta"]) - target_delta))


def interpolate_iv_to_tenor(
    iv_points: list[tuple[float, float]],  # (days, iv)
    target_days: float,
) -> float | None:
    """
    Linearly interpolate IV to target tenor.

    Args:
        iv_points: List of (days_to_expiry, iv) tuples, sorted by days
        target_days: Target days for interpolation

    Returns:
        Interpolated IV or None if not possible
    """
    if not iv_points:
        return None

    if len(iv_points) == 1:
        return iv_points[0][1]

    # Sort by days
    sorted_points = sorted(iv_points, key=lambda x: x[0])

    # Find bracketing points
    for i in range(len(sorted_points) - 1):
        d1, iv1 = sorted_points[i]
        d2, iv2 = sorted_points[i + 1]

        if d1 <= target_days <= d2:
            # Linear interpolation
            weight = (target_days - d1) / (d2 - d1) if d2 != d1 else 0.5
            return iv1 + weight * (iv2 - iv1)

    # Extrapolate if target is outside range
    if target_days < sorted_points[0][0]:
        return sorted_points[0][1]  # Use nearest
    if target_days > sorted_points[-1][0]:
        return sorted_points[-1][1]  # Use nearest

    return None


def dvol_to_decimal(dvol_value: float) -> float:
    """
    Convert DVOL index value to decimal form.

    DVOL is typically expressed as a percentage (e.g., 80 for 80% IV).
    This converts to decimal form (0.80) for calculations.
    """
    # DVOL is already in percentage form (e.g., 80.5)
    return dvol_value / 100.0


def calculate_forward_price(
    spot: float,
    rate: float,
    time_years: float,
) -> float:
    """
    Calculate forward price.

    F = S * e^(r*t)

    For crypto, rate is often approximated from futures basis or set to 0.
    """
    return spot * math.exp(rate * time_years)


def estimate_forward_from_futures(
    spot: float,
    futures_price: float,
    time_years: float,
) -> float | None:
    """
    Estimate implied risk-free rate from futures price.

    r = ln(F/S) / t
    """
    if spot <= 0 or futures_price <= 0 or time_years <= 0:
        return None
    return math.log(futures_price / spot) / time_years


def calculate_imbalance(bid_depth: float, ask_depth: float) -> float | None:
    """
    Calculate order book imbalance.

    Imbalance = (bid_depth - ask_depth) / (bid_depth + ask_depth)

    Returns value between -1 (all asks) and 1 (all bids).
    """
    total = bid_depth + ask_depth
    if total == 0:
        return None
    return (bid_depth - ask_depth) / total


def spread_in_bps(bid: float, ask: float) -> float | None:
    """Calculate spread in basis points relative to mid price."""
    if bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2
    if mid == 0:
        return None
    spread = ask - bid
    return (spread / mid) * 10000


# =============================================================================
# Option Chain Analysis
# =============================================================================


@dataclass
class OptionData:
    """Option data for a single strike."""

    strike: float
    option_type: Literal["call", "put"]
    instrument_name: str
    mark_iv: float | None = None  # IV as decimal (0.80 = 80%)
    delta: float | None = None
    gamma: float | None = None
    vega: float | None = None
    theta: float | None = None
    open_interest: float | None = None
    volume_24h: float | None = None
    mark_price: float | None = None
    underlying_price: float | None = None


@dataclass
class StrikeOI:
    """Open interest aggregated by strike."""

    strike: float
    call_oi: float = 0.0
    put_oi: float = 0.0
    total_oi: float = 0.0
    put_call_ratio: float | None = None  # put_oi / call_oi

    def __post_init__(self):
        self.total_oi = self.call_oi + self.put_oi
        if self.call_oi > 0:
            self.put_call_ratio = self.put_oi / self.call_oi


@dataclass
class GEXResult:
    """Gamma Exposure calculation result."""

    strike: float
    call_gex: float = 0.0  # Call gamma exposure (positive = long gamma)
    put_gex: float = 0.0  # Put gamma exposure (negative = short gamma for puts)
    net_gex: float = 0.0  # call_gex + put_gex


@dataclass
class GammaExposureProfile:
    """Complete gamma exposure profile."""

    gex_by_strike: list[GEXResult]
    net_gex: float  # Total net GEX across all strikes
    gamma_flip_level: float | None  # Price where net GEX crosses zero
    max_positive_gex_strike: float | None  # Strike with highest positive GEX
    max_negative_gex_strike: float | None  # Strike with highest negative GEX
    spot_price: float
    calculation_notes: list[str] = field(default_factory=list)


@dataclass
class MaxPainResult:
    """Max pain calculation result."""

    max_pain_strike: float
    pain_value: float  # Total $ pain at max pain strike
    pain_curve: list[tuple[float, float]]  # [(strike, pain_value), ...]
    pain_curve_top3: list[tuple[float, float]]  # Top 3 strikes with lowest pain
    total_call_oi: float
    total_put_oi: float
    spot_price: float


@dataclass
class TermStructurePoint:
    """Single point on the IV term structure."""

    days: int  # Days to expiry
    atm_iv: float | None  # ATM IV as decimal
    expiry_label: str  # e.g., "28JUN24"
    expiry_ts: int  # Expiry timestamp in ms


@dataclass
class IVTermStructure:
    """Complete IV term structure."""

    currency: str
    spot_price: float
    term_structure: list[TermStructurePoint]
    slope_short: float | None  # Slope 7d-30d (IV change per day)
    slope_long: float | None  # Slope 30d-90d
    contango: bool  # True if IV increases with time
    notes: list[str] = field(default_factory=list)


@dataclass
class SkewMetrics:
    """Skew metrics for a specific tenor."""

    days: int
    rr25d: float | None  # 25-delta risk reversal
    bf25d: float | None  # 25-delta butterfly
    atm_iv: float | None
    call_25d_iv: float | None
    put_25d_iv: float | None
    skew_direction: Literal["bullish", "bearish", "neutral"] | None


@dataclass
class SkewAnalysis:
    """Complete skew analysis."""

    currency: str
    spot_price: float
    skew_by_tenor: list[SkewMetrics]
    skew_trend: Literal["steepening", "flattening", "stable"] | None
    notes: list[str] = field(default_factory=list)


# =============================================================================
# Gamma Exposure (GEX) Calculations
# =============================================================================


def calculate_single_gex(
    gamma: float,
    open_interest: float,
    spot_price: float,
    option_type: Literal["call", "put"],
    contract_size: float = STANDARD_CONTRACT_SIZE,
) -> float:
    """
    Calculate gamma exposure for a single option.

    GEX Formula (simplified):
        GEX = gamma * OI * spot^2 * contract_size / 100

    For dealer positioning (assuming retail is net long options):
    - Calls: Dealers are short gamma (negative GEX)
    - Puts: Dealers are long gamma (positive GEX)

    The sign convention here assumes we're measuring from dealer's perspective.

    Args:
        gamma: Option gamma (per 1 point move in underlying)
        open_interest: Number of contracts
        spot_price: Current spot/underlying price
        option_type: 'call' or 'put'
        contract_size: Contract size (default 1 BTC)

    Returns:
        GEX value in $ terms (scaled)
    """
    if gamma is None or gamma == 0 or open_interest is None or open_interest == 0:
        return 0.0

    # Basic GEX calculation: how much delta changes for a $1 move in spot
    # Gamma is dDelta/dSpot, so gamma * spot gives $ gamma exposure per contract
    # OI * gamma * spot^2 * 0.01 gives the total $ gamma exposure (scaled by 1%)
    raw_gex = gamma * open_interest * spot_price * spot_price * contract_size * 0.01

    # Sign convention (dealer perspective):
    # - Dealers are typically short calls (customers long calls) → negative gamma exposure
    # - Dealers are typically short puts (customers long puts) → positive gamma exposure
    if option_type == "call":
        return -raw_gex  # Dealers short calls = negative gamma
    else:
        return raw_gex  # Dealers short puts = positive gamma (puts have negative gamma)


def calculate_gamma_exposure_profile(
    options: list[OptionData],
    spot_price: float,
) -> GammaExposureProfile:
    """
    Calculate complete gamma exposure profile from option data.

    Args:
        options: List of OptionData with gamma and OI
        spot_price: Current spot price

    Returns:
        GammaExposureProfile with GEX by strike and summary metrics
    """
    notes: list[str] = []

    # Group options by strike
    strikes_data: dict[float, GEXResult] = {}

    for opt in options:
        if opt.gamma is None or opt.open_interest is None:
            continue

        strike = opt.strike
        if strike not in strikes_data:
            strikes_data[strike] = GEXResult(strike=strike)

        gex_value = calculate_single_gex(
            gamma=opt.gamma,
            open_interest=opt.open_interest,
            spot_price=spot_price,
            option_type=opt.option_type,
        )

        if opt.option_type == "call":
            strikes_data[strike].call_gex = gex_value
        else:
            strikes_data[strike].put_gex = gex_value

    # Calculate net GEX per strike
    for _strike, gex in strikes_data.items():
        gex.net_gex = gex.call_gex + gex.put_gex

    # Sort by strike
    gex_by_strike = sorted(strikes_data.values(), key=lambda x: x.strike)

    # Calculate total net GEX
    net_gex = sum(g.net_gex for g in gex_by_strike)

    # Find gamma flip level (where net GEX crosses zero)
    gamma_flip_level = None
    for i in range(len(gex_by_strike) - 1):
        curr = gex_by_strike[i]
        next_g = gex_by_strike[i + 1]
        # Sign change and not equal (to avoid division issues)
        if curr.net_gex * next_g.net_gex < 0 and next_g.net_gex != curr.net_gex:
            # Linear interpolation
            ratio = abs(curr.net_gex) / abs(next_g.net_gex - curr.net_gex)
            gamma_flip_level = curr.strike + ratio * (next_g.strike - curr.strike)
            break

    # Find max positive/negative GEX strikes
    max_positive_gex_strike = None
    max_negative_gex_strike = None
    max_positive_value = 0.0
    max_negative_value = 0.0

    for gex in gex_by_strike:
        if gex.net_gex > max_positive_value:
            max_positive_value = gex.net_gex
            max_positive_gex_strike = gex.strike
        if gex.net_gex < max_negative_value:
            max_negative_value = gex.net_gex
            max_negative_gex_strike = gex.strike

    if not gex_by_strike:
        notes.append("no_valid_gamma_data")

    return GammaExposureProfile(
        gex_by_strike=gex_by_strike,
        net_gex=net_gex,
        gamma_flip_level=gamma_flip_level,
        max_positive_gex_strike=max_positive_gex_strike,
        max_negative_gex_strike=max_negative_gex_strike,
        spot_price=spot_price,
        calculation_notes=notes,
    )


# =============================================================================
# Max Pain Calculations
# =============================================================================


def calculate_pain_at_strike(
    target_strike: float,
    options: list[OptionData],
) -> float:
    """
    Calculate total "pain" (loss for option buyers) if underlying expires at target_strike.

    Pain = sum of intrinsic values for all ITM options (what option writers keep)

    For calls: pain = max(0, target_strike - strike) * OI
    For puts:  pain = max(0, strike - target_strike) * OI
    """
    total_pain = 0.0

    for opt in options:
        if opt.open_interest is None or opt.open_interest == 0:
            continue

        if opt.option_type == "call":
            # Call is ITM if target > strike
            intrinsic = max(0, target_strike - opt.strike)
        else:
            # Put is ITM if target < strike
            intrinsic = max(0, opt.strike - target_strike)

        total_pain += intrinsic * opt.open_interest

    return total_pain


def calculate_max_pain(
    options: list[OptionData],
    spot_price: float,
) -> MaxPainResult:
    """
    Calculate max pain strike - the price where option buyers lose the most.

    The max pain theory suggests that the underlying tends to gravitate
    toward the strike price where the total $ value of expiring options
    is minimized for option holders (maximized pain).

    Args:
        options: List of all options for an expiry
        spot_price: Current spot price

    Returns:
        MaxPainResult with max pain strike and pain curve
    """
    # Get unique strikes
    strikes = sorted({opt.strike for opt in options if opt.strike is not None})

    if not strikes:
        return MaxPainResult(
            max_pain_strike=spot_price,
            pain_value=0,
            pain_curve=[],
            pain_curve_top3=[],
            total_call_oi=0,
            total_put_oi=0,
            spot_price=spot_price,
        )

    # Calculate pain at each strike
    pain_curve: list[tuple[float, float]] = []
    for strike in strikes:
        pain = calculate_pain_at_strike(strike, options)
        pain_curve.append((strike, pain))

    # Find max pain (minimum pain value = where options expire worthless)
    # We want the strike where option BUYERS have maximum loss
    # which means where the total ITM value is MINIMUM for buyers
    # Actually, max pain is where total $ of options expiring ITM is minimized
    min_pain = float("inf")
    max_pain_strike = spot_price

    for strike, pain in pain_curve:
        if pain < min_pain:
            min_pain = pain
            max_pain_strike = strike

    # Get top 3 strikes with lowest pain
    sorted_by_pain = sorted(pain_curve, key=lambda x: x[1])
    pain_curve_top3 = sorted_by_pain[:3]

    # Calculate total OI
    total_call_oi = sum(opt.open_interest or 0 for opt in options if opt.option_type == "call")
    total_put_oi = sum(opt.open_interest or 0 for opt in options if opt.option_type == "put")

    return MaxPainResult(
        max_pain_strike=max_pain_strike,
        pain_value=min_pain,
        pain_curve=pain_curve,
        pain_curve_top3=pain_curve_top3,
        total_call_oi=total_call_oi,
        total_put_oi=total_put_oi,
        spot_price=spot_price,
    )


# =============================================================================
# Open Interest Analysis
# =============================================================================


def aggregate_oi_by_strike(options: list[OptionData]) -> list[StrikeOI]:
    """
    Aggregate call/put open interest by strike.

    Args:
        options: List of options with OI data

    Returns:
        List of StrikeOI sorted by strike
    """
    by_strike: dict[float, StrikeOI] = {}

    for opt in options:
        if opt.strike is None or opt.open_interest is None:
            continue

        strike = opt.strike
        if strike not in by_strike:
            by_strike[strike] = StrikeOI(strike=strike)

        if opt.option_type == "call":
            by_strike[strike].call_oi += opt.open_interest
        else:
            by_strike[strike].put_oi += opt.open_interest

    # Recalculate totals and ratios
    result = []
    for oi in by_strike.values():
        oi.total_oi = oi.call_oi + oi.put_oi
        if oi.call_oi > 0:
            oi.put_call_ratio = oi.put_oi / oi.call_oi
        result.append(oi)

    return sorted(result, key=lambda x: x.strike)


def find_oi_peaks(
    oi_data: list[StrikeOI],
    top_n: int = 5,
) -> list[StrikeOI]:
    """
    Find strikes with highest OI concentration.

    Args:
        oi_data: List of StrikeOI
        top_n: Number of top strikes to return

    Returns:
        Top N strikes sorted by total OI (descending)
    """
    return sorted(oi_data, key=lambda x: x.total_oi, reverse=True)[:top_n]


def find_oi_peak_range(
    oi_data: list[StrikeOI],
    percentile: float = 0.8,
) -> tuple[float, float] | None:
    """
    Find the strike range containing the top percentile of OI.

    Args:
        oi_data: List of StrikeOI
        percentile: Cumulative OI threshold (0.8 = 80%)

    Returns:
        Tuple of (low_strike, high_strike) or None
    """
    if not oi_data:
        return None

    total_oi = sum(d.total_oi for d in oi_data)
    if total_oi == 0:
        return None

    # Sort by OI descending
    sorted_oi = sorted(oi_data, key=lambda x: x.total_oi, reverse=True)

    # Find strikes that make up the percentile
    cumulative = 0.0
    peak_strikes = []
    for oi in sorted_oi:
        cumulative += oi.total_oi / total_oi
        peak_strikes.append(oi.strike)
        if cumulative >= percentile:
            break

    if not peak_strikes:
        return None

    return (min(peak_strikes), max(peak_strikes))


# =============================================================================
# IV Term Structure
# =============================================================================


def calculate_iv_term_structure_slope(
    points: list[TermStructurePoint],
    start_days: int,
    end_days: int,
) -> float | None:
    """
    Calculate IV term structure slope between two tenors.

    Slope = (IV_end - IV_start) / (days_end - days_start) * 30
    Result is IV change per 30 days.

    Args:
        points: List of TermStructurePoint
        start_days: Starting tenor in days
        end_days: Ending tenor in days

    Returns:
        Slope (IV change per 30 days) or None
    """
    # Find closest points to start and end
    start_point = None
    end_point = None

    for p in points:
        if p.atm_iv is None:
            continue
        if (
            (start_point is None or abs(p.days - start_days) < abs(start_point.days - start_days))
            and abs(p.days - start_days) < start_days * 0.5
        ):
            start_point = p
        if (
            (end_point is None or abs(p.days - end_days) < abs(end_point.days - end_days))
            and abs(p.days - end_days) < end_days * 0.5
        ):
            end_point = p

    if start_point is None or end_point is None:
        return None
    if start_point.atm_iv is None or end_point.atm_iv is None:
        return None
    if start_point.days == end_point.days:
        return None

    # Slope per 30 days
    iv_diff = end_point.atm_iv - start_point.atm_iv
    days_diff = end_point.days - start_point.days

    return (iv_diff / days_diff) * 30


def analyze_term_structure_shape(points: list[TermStructurePoint]) -> bool:
    """
    Determine if term structure is in contango (upward sloping).

    Returns True if IV generally increases with time.
    """
    valid_points = [p for p in points if p.atm_iv is not None]
    if len(valid_points) < 2:
        return True  # Default to contango

    # Simple: compare first and last
    sorted_points = sorted(valid_points, key=lambda x: x.days)
    first_iv = sorted_points[0].atm_iv
    last_iv = sorted_points[-1].atm_iv

    if first_iv is not None and last_iv is not None:
        return last_iv > first_iv

    return True


# =============================================================================
# Skew Metrics
# =============================================================================


def determine_skew_direction(
    rr25d: float | None,
    threshold: float = 0.005,  # 0.5% threshold
) -> Literal["bullish", "bearish", "neutral"] | None:
    """
    Determine skew direction from risk reversal.

    Args:
        rr25d: 25-delta risk reversal (call IV - put IV)
        threshold: Threshold for neutral determination

    Returns:
        Skew direction or None if insufficient data
    """
    if rr25d is None:
        return None

    if rr25d > threshold:
        return "bullish"  # Calls more expensive, upside demand
    elif rr25d < -threshold:
        return "bearish"  # Puts more expensive, downside protection
    else:
        return "neutral"


def determine_skew_trend(
    skew_metrics: list[SkewMetrics],
) -> Literal["steepening", "flattening", "stable"] | None:
    """
    Determine if skew is steepening or flattening across tenors.

    Steepening: Short-term skew becoming more extreme vs long-term
    Flattening: Short and long-term skew converging
    """
    valid = [m for m in skew_metrics if m.rr25d is not None]
    if len(valid) < 2:
        return None

    sorted_metrics = sorted(valid, key=lambda x: x.days)

    # Compare short-term vs long-term
    short_term = sorted_metrics[0]
    long_term = sorted_metrics[-1]

    if short_term.rr25d is None or long_term.rr25d is None:
        return None

    # Compare absolute values
    short_abs = abs(short_term.rr25d)
    long_abs = abs(long_term.rr25d)

    diff = short_abs - long_abs
    if diff > 0.01:  # 1% threshold
        return "steepening"  # Short-term skew more extreme
    elif diff < -0.01:
        return "flattening"  # Long-term skew more extreme
    else:
        return "stable"


def estimate_25d_strike(
    spot: float,
    atm_iv: float,
    days_to_expiry: float,
    is_call: bool,
) -> float:
    """
    Estimate the 25-delta strike price.

    Uses simplified formula based on Black-Scholes:
    K_25d ≈ S * exp(±0.675 * σ * √T)

    Where:
    - 0.675 is the inverse normal CDF of 0.25 (approximately)
    - Positive for calls (OTM call strike is above spot)
    - Negative for puts (OTM put strike is below spot)

    Args:
        spot: Current spot price
        atm_iv: ATM implied volatility (decimal)
        days_to_expiry: Days until expiration
        is_call: True for call, False for put

    Returns:
        Estimated 25-delta strike price
    """
    if atm_iv <= 0 or days_to_expiry <= 0:
        return spot

    t_years = days_to_expiry / DAYS_PER_YEAR
    z = 0.675  # Approx inverse normal CDF of 0.25

    if is_call:
        # 25d call is OTM, strike above spot
        return spot * math.exp(z * atm_iv * math.sqrt(t_years))
    else:
        # 25d put is OTM, strike below spot
        return spot * math.exp(-z * atm_iv * math.sqrt(t_years))


def find_closest_strike_option(
    options: list[OptionData],
    target_strike: float,
    option_type: Literal["call", "put"],
) -> OptionData | None:
    """Find option closest to target strike."""
    filtered = [o for o in options if o.option_type == option_type and o.strike is not None]
    if not filtered:
        return None

    return min(filtered, key=lambda x: abs(x.strike - target_strike))
