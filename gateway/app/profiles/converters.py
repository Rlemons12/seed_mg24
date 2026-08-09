from bisect import bisect_right
from collections.abc import Callable

from gateway.app.profiles.models import ConversionDefinition


class ConversionError(ValueError):
    pass


def convert_value(value: float, conversion: ConversionDefinition) -> float | None:
    if conversion.type == "unconfigured":
        return None
    if conversion.type == "identity":
        return value
    if conversion.type == "linear":
        return value * conversion.gain + conversion.offset
    if conversion.type == "polynomial":
        return sum(coefficient * value**degree for degree, coefficient in enumerate(conversion.coefficients))
    if conversion.type in {"piecewise_linear", "lookup_table"}:
        points = conversion.points
        index = max(0, min(bisect_right([point[0] for point in points], value) - 1, len(points) - 2))
        left, right = points[index], points[index + 1]
        if conversion.type == "lookup_table":
            return min(points, key=lambda point: abs(point[0] - value))[1]
        fraction = (value - left[0]) / (right[0] - left[0])
        return left[1] + fraction * (right[1] - left[1])
    raise ConversionError("unsupported allowlisted conversion")


CONVERTER_REGISTRY: dict[str, Callable[[float, ConversionDefinition], float | None]] = {
    name: convert_value for name in ("unconfigured", "identity", "linear", "piecewise_linear", "polynomial", "lookup_table")
}
