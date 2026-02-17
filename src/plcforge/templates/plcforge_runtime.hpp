// plcforge runtime for translated IEC 61131-3 programs.
//
// Header only, C++17, no dependencies beyond the standard library.  Drop this
// file next to the generated headers and compile; there is nothing to link.
//
// The scan clock is injectable so that timers can be driven by a simulated
// clock in a unit test and by the steady clock in production.

#ifndef PLCFORGE_RUNTIME_HPP
#define PLCFORGE_RUNTIME_HPP

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <functional>
#include <initializer_list>
#include <limits>
#include <stdexcept>
#include <string>
#include <thread>
#include <type_traits>

namespace plcforge {

// --------------------------------------------------------------------------
// Elementary types
// --------------------------------------------------------------------------

using BOOL = bool;
using SINT = std::int8_t;
using INT = std::int16_t;
using DINT = std::int32_t;
using LINT = std::int64_t;
using USINT = std::uint8_t;
using UINT = std::uint16_t;
using UDINT = std::uint32_t;
using ULINT = std::uint64_t;
using BYTE = std::uint8_t;
using WORD = std::uint16_t;
using DWORD = std::uint32_t;
using LWORD = std::uint64_t;
using REAL = float;
using LREAL = double;
using STRING = std::string;

/// Durations are seconds, matching the unit used by every timer.
using TIME = double;

/// Parse the seconds value of an IEC duration literal at compile time is not
/// possible in C++17, so the generator emits the numeric value directly and
/// this helper exists only for hand written call sites.
inline constexpr TIME seconds(double value) noexcept { return value; }

// --------------------------------------------------------------------------
// Clock
// --------------------------------------------------------------------------

/// Time source for the scan cycle.  Replace it to make timers deterministic.
class Clock {
public:
    virtual ~Clock() = default;
    virtual TIME now() const = 0;
};

/// Monotonic wall clock, measured from the moment it was constructed.
class MonotonicClock final : public Clock {
public:
    MonotonicClock() : origin_(std::chrono::steady_clock::now()) {}

    TIME now() const override {
        const auto delta = std::chrono::steady_clock::now() - origin_;
        return std::chrono::duration<TIME>(delta).count();
    }

private:
    std::chrono::steady_clock::time_point origin_;
};

/// A clock that only advances when the test asks it to.
class SimulatedClock final : public Clock {
public:
    explicit SimulatedClock(TIME start = 0.0) : seconds_(start) {}

    TIME now() const override { return seconds_; }

    TIME advance(TIME delta) {
        if (delta < 0.0) {
            throw std::invalid_argument("a simulated clock cannot run backwards");
        }
        seconds_ += delta;
        return seconds_;
    }

    void set(TIME value) { seconds_ = value; }

private:
    TIME seconds_;
};

/// The clock every function block reads unless one is injected explicitly.
inline Clock*& active_clock() {
    static MonotonicClock fallback;
    static Clock* current = &fallback;
    return current;
}

inline void set_clock(Clock* clock) {
    active_clock() = clock;
}

inline TIME now() { return active_clock()->now(); }

// --------------------------------------------------------------------------
// Arrays with IEC index bounds
// --------------------------------------------------------------------------

/// ``ARRAY [Lower..Upper] OF T`` keeps its declared index origin.
template <typename T, int Lower, int Upper>
class Array {
    static_assert(Upper >= Lower, "array upper bound must not be below the lower bound");

public:
    static constexpr int lower = Lower;
    static constexpr int upper = Upper;
    static constexpr std::size_t size = static_cast<std::size_t>(Upper - Lower + 1);

    Array() : data_{} {}
    explicit Array(const T& fill) { data_.fill(fill); }

    /// Braced initialiser matching an IEC array initial value list.  Missing
    /// trailing values keep the element default, as they do on the controller.
    Array(std::initializer_list<T> values) : data_{} {
        if (values.size() > size) {
            throw std::out_of_range("array initialiser has more values than the array holds");
        }
        std::copy(values.begin(), values.end(), data_.begin());
    }

    T& operator[](int index) { return data_[offset(index)]; }
    const T& operator[](int index) const { return data_[offset(index)]; }

    T* begin() { return data_.data(); }
    T* end() { return data_.data() + size; }
    const T* begin() const { return data_.data(); }
    const T* end() const { return data_.data() + size; }

private:
    static std::size_t offset(int index) {
        if (index < Lower || index > Upper) {
            throw std::out_of_range("array index " + std::to_string(index) + " outside declared bounds");
        }
        return static_cast<std::size_t>(index - Lower);
    }

    std::array<T, size> data_;
};

// --------------------------------------------------------------------------
// Standard functions
// --------------------------------------------------------------------------

/// Integer division truncating toward zero, matching both IEC and C++11.
template <typename T>
constexpr T idiv(T dividend, T divisor) {
    if (divisor == T{0}) {
        throw std::domain_error("integer division by zero");
    }
    return static_cast<T>(dividend / divisor);
}

/// Remainder following the sign of the dividend, as IEC requires.
template <typename T>
constexpr T imod(T dividend, T divisor) {
    if (divisor == T{0}) {
        throw std::domain_error("modulo by zero");
    }
    return static_cast<T>(dividend % divisor);
}

// The selection functions take heterogeneous arguments on purpose: PLC sources
// routinely mix REAL and LREAL literals, and refusing to deduce a common type
// would force a cast into every generated call site.

template <typename T>
constexpr T ABS(T value) { return value < T{0} ? static_cast<T>(-value) : value; }

template <typename A, typename B, typename R = std::common_type_t<A, B>>
constexpr R MIN(A a, B b) { return static_cast<R>(a) < static_cast<R>(b) ? static_cast<R>(a) : static_cast<R>(b); }

template <typename A, typename B, typename R = std::common_type_t<A, B>>
constexpr R MAX(A a, B b) { return static_cast<R>(a) > static_cast<R>(b) ? static_cast<R>(a) : static_cast<R>(b); }

/// Clamp *value* into [minimum, maximum] using the IEC argument order.
template <typename A, typename B, typename C, typename R = std::common_type_t<A, B, C>>
constexpr R LIMIT(A minimum, B value, C maximum) {
    const R low = static_cast<R>(minimum);
    const R high = static_cast<R>(maximum);
    const R current = static_cast<R>(value);
    return current < low ? low : (current > high ? high : current);
}

template <typename A, typename B, typename R = std::common_type_t<A, B>>
constexpr R SEL(bool selector, A on_false, B on_true) {
    return selector ? static_cast<R>(on_true) : static_cast<R>(on_false);
}

inline LREAL SQRT(LREAL value) { return std::sqrt(value); }
inline LREAL LN(LREAL value) { return std::log(value); }
inline LREAL LOG(LREAL value) { return std::log10(value); }
inline LREAL EXP(LREAL value) { return std::exp(value); }
inline DINT TRUNC(LREAL value) { return static_cast<DINT>(std::trunc(value)); }
inline DINT ROUND(LREAL value) { return static_cast<DINT>(value >= 0 ? std::floor(value + 0.5) : std::ceil(value - 0.5)); }
inline INT LEN(const STRING& value) { return static_cast<INT>(value.size()); }
inline STRING CONCAT(const STRING& a, const STRING& b) { return a + b; }
inline STRING LEFT(const STRING& value, INT count) { return value.substr(0, static_cast<std::size_t>(MAX<INT>(count, 0))); }
inline STRING RIGHT(const STRING& value, INT count) {
    const auto take = static_cast<std::size_t>(MAX<INT>(count, 0));
    return take >= value.size() ? value : value.substr(value.size() - take);
}
/// One-based substring, as in IEC.
inline STRING MID(const STRING& value, INT count, INT position) {
    const auto start = static_cast<std::size_t>(MAX<INT>(position - 1, 0));
    if (start >= value.size()) { return STRING{}; }
    return value.substr(start, static_cast<std::size_t>(MAX<INT>(count, 0)));
}
/// One-based position of the needle, or zero when it does not occur.
inline INT FIND(const STRING& haystack, const STRING& needle) {
    const auto at = haystack.find(needle);
    return at == STRING::npos ? 0 : static_cast<INT>(at + 1);
}

// --------------------------------------------------------------------------
// Standard function blocks
// --------------------------------------------------------------------------

/// Base class: holds the optional per-instance clock override.
class FunctionBlock {
public:
    explicit FunctionBlock(Clock* clock = nullptr) : clock_(clock) {}
    virtual ~FunctionBlock() = default;

    TIME time() const { return clock_ ? clock_->now() : now(); }
    void set_clock(Clock* clock) { clock_ = clock; }

protected:
    Clock* clock_ = nullptr;
};

/// On-delay timer: Q goes high once IN has been high for PT.
class TON : public FunctionBlock {
public:
    using FunctionBlock::FunctionBlock;

    BOOL IN = false;
    TIME PT = 0.0;
    BOOL Q = false;
    TIME ET = 0.0;

    void operator()() {
        if (IN) {
            const TIME current = time();
            if (!running_) { started_ = current; running_ = true; }
            ET = MIN(current - started_, PT);
            Q = ET >= PT;
        } else {
            running_ = false;
            ET = 0.0;
            Q = false;
        }
    }

    void operator()(BOOL in, TIME pt) { IN = in; PT = pt; (*this)(); }

    void reset() { running_ = false; ET = 0.0; Q = false; }

private:
    TIME started_ = 0.0;
    bool running_ = false;
};

/// Off-delay timer: Q drops PT after IN goes low.
class TOF : public FunctionBlock {
public:
    using FunctionBlock::FunctionBlock;

    BOOL IN = false;
    TIME PT = 0.0;
    BOOL Q = false;
    TIME ET = 0.0;

    void operator()() {
        if (IN) {
            timing_ = false;
            ET = 0.0;
            Q = true;
        } else if (!timing_ && Q) {
            timing_ = true;
            released_ = time();
        }
        if (timing_) {
            ET = MIN(time() - released_, PT);
            if (ET >= PT) { Q = false; }
        }
    }

    void operator()(BOOL in, TIME pt) { IN = in; PT = pt; (*this)(); }

    void reset() { timing_ = false; ET = 0.0; Q = false; }

private:
    TIME released_ = 0.0;
    bool timing_ = false;
};

/// Pulse timer: a rising edge on IN holds Q high for PT.
class TP : public FunctionBlock {
public:
    using FunctionBlock::FunctionBlock;

    BOOL IN = false;
    TIME PT = 0.0;
    BOOL Q = false;
    TIME ET = 0.0;

    void operator()() {
        const TIME current = time();
        if (IN && !previous_ && !running_) {
            started_ = current;
            running_ = true;
        }
        if (running_) {
            ET = MIN(current - started_, PT);
            Q = ET < PT;
            if (!Q && !IN) { running_ = false; ET = 0.0; }
        } else {
            Q = false;
            ET = 0.0;
        }
        previous_ = IN;
    }

    void operator()(BOOL in, TIME pt) { IN = in; PT = pt; (*this)(); }

    void reset() { running_ = false; previous_ = false; ET = 0.0; Q = false; }

private:
    TIME started_ = 0.0;
    bool running_ = false;
    bool previous_ = false;
};

/// Up counter.
class CTU : public FunctionBlock {
public:
    using FunctionBlock::FunctionBlock;

    BOOL CU = false;
    BOOL R = false;
    INT PV = 0;
    BOOL Q = false;
    INT CV = 0;

    void operator()() {
        if (R) {
            CV = 0;
        } else if (CU && !previous_ && CV < std::numeric_limits<INT>::max()) {
            ++CV;
        }
        Q = CV >= PV;
        previous_ = CU;
    }

    void reset() { CV = 0; Q = false; previous_ = false; }

private:
    bool previous_ = false;
};

/// Down counter.
class CTD : public FunctionBlock {
public:
    using FunctionBlock::FunctionBlock;

    BOOL CD = false;
    BOOL LD = false;
    INT PV = 0;
    BOOL Q = false;
    INT CV = 0;

    void operator()() {
        if (LD) {
            CV = PV;
        } else if (CD && !previous_ && CV > std::numeric_limits<INT>::min()) {
            --CV;
        }
        Q = CV <= 0;
        previous_ = CD;
    }

    void reset() { CV = 0; Q = false; previous_ = false; }

private:
    bool previous_ = false;
};

/// Combined up/down counter.
class CTUD : public FunctionBlock {
public:
    using FunctionBlock::FunctionBlock;

    BOOL CU = false;
    BOOL CD = false;
    BOOL R = false;
    BOOL LD = false;
    INT PV = 0;
    BOOL QU = false;
    BOOL QD = true;
    INT CV = 0;

    void operator()() {
        const bool rising_up = CU && !previous_up_;
        const bool rising_down = CD && !previous_down_;
        if (R) {
            CV = 0;
        } else if (LD) {
            CV = PV;
        } else if (rising_up && !rising_down && CV < std::numeric_limits<INT>::max()) {
            ++CV;
        } else if (rising_down && !rising_up && CV > std::numeric_limits<INT>::min()) {
            --CV;
        }
        QU = CV >= PV;
        QD = CV <= 0;
        previous_up_ = CU;
        previous_down_ = CD;
    }

    void reset() { CV = 0; QU = false; QD = true; previous_up_ = false; previous_down_ = false; }

private:
    bool previous_up_ = false;
    bool previous_down_ = false;
};

/// Rising edge detector: Q is high for exactly one scan.
class R_TRIG : public FunctionBlock {
public:
    using FunctionBlock::FunctionBlock;

    BOOL CLK = false;
    BOOL Q = false;

    void operator()() { Q = CLK && !previous_; previous_ = CLK; }
    void reset() { Q = false; previous_ = false; }

private:
    bool previous_ = false;
};

/// Falling edge detector: Q is high for exactly one scan.
class F_TRIG : public FunctionBlock {
public:
    using FunctionBlock::FunctionBlock;

    BOOL CLK = false;
    BOOL Q = false;

    void operator()() { Q = !CLK && previous_; previous_ = CLK; }
    void reset() { Q = false; previous_ = true; }

private:
    bool previous_ = true;
};

/// Set dominant bistable.
class SR : public FunctionBlock {
public:
    using FunctionBlock::FunctionBlock;

    BOOL S1 = false;
    BOOL R = false;
    BOOL Q1 = false;

    void operator()() { Q1 = S1 || (Q1 && !R); }
    void reset() { Q1 = false; }
};

/// Reset dominant bistable.
class RS : public FunctionBlock {
public:
    using FunctionBlock::FunctionBlock;

    BOOL S = false;
    BOOL R1 = false;
    BOOL Q1 = false;

    void operator()() { Q1 = !R1 && (S || Q1); }
    void reset() { Q1 = false; }
};

// --------------------------------------------------------------------------
// Scan cycle
// --------------------------------------------------------------------------

/// Base class for a translated PROGRAM.
class Program : public FunctionBlock {
public:
    using FunctionBlock::FunctionBlock;
    virtual void scan() = 0;
};

/// Runs a Program on a fixed cycle, counting overruns.
class ScanEngine {
public:
    ScanEngine(Program& program, TIME cycle_time = 0.01)
        : program_(program), cycle_time_(cycle_time) {
        if (cycle_time <= 0.0) {
            throw std::invalid_argument("cycle_time must be positive");
        }
    }

    void scan_once() {
        const auto started = std::chrono::steady_clock::now();
        if (read_inputs) { read_inputs(); }
        program_.scan();
        if (write_outputs) { write_outputs(); }
        const auto duration = std::chrono::duration<TIME>(std::chrono::steady_clock::now() - started).count();
        ++cycles;
        total_duration += duration;
        max_duration = MAX(max_duration, duration);
        if (duration > cycle_time_) { ++overruns; }
    }

    void run_cycles(std::size_t count, SimulatedClock* simulated = nullptr) {
        for (std::size_t index = 0; index < count; ++index) {
            scan_once();
            if (simulated) { simulated->advance(cycle_time_); }
        }
    }

    /// Runs until stop() is called, holding the configured cycle time.
    void run_forever() {
        stopped_ = false;
        auto deadline = std::chrono::steady_clock::now();
        while (!stopped_) {
            scan_once();
            deadline += std::chrono::duration_cast<std::chrono::steady_clock::duration>(
                std::chrono::duration<TIME>(cycle_time_));
            const auto current = std::chrono::steady_clock::now();
            if (deadline > current) {
                std::this_thread::sleep_until(deadline);
            } else {
                // Behind schedule: drop the missed slots instead of catching up.
                deadline = current;
            }
        }
    }

    void stop() { stopped_ = true; }

    TIME average_duration() const { return cycles ? total_duration / static_cast<TIME>(cycles) : 0.0; }
    TIME cycle_time() const { return cycle_time_; }

    std::function<void()> read_inputs;
    std::function<void()> write_outputs;

    std::size_t cycles = 0;
    std::size_t overruns = 0;
    TIME total_duration = 0.0;
    TIME max_duration = 0.0;

private:
    Program& program_;
    TIME cycle_time_;
    bool stopped_ = false;
};

}  // namespace plcforge

#endif  // PLCFORGE_RUNTIME_HPP
