// Recessive axis: hairline gridlines + tick labels in text tokens. Draws at most
// one y axis (the Chart owns one yScale) and an optional x baseline with category
// labels — never a second value axis.
import { useChart } from "./chartContext";
import { niceTicks } from "./scales";

export interface AxisProps {
  /** Draw horizontal gridlines + y tick labels. */
  y?: boolean;
  /** Approx number of y ticks. Default 4. */
  yTicks?: number;
  /** Format a y tick value. Default `String`. */
  yFormat?: (v: number) => string;
  /** Draw the x baseline. */
  x?: boolean;
  /** One label per x position (index i → xScale(i)). */
  xLabels?: readonly string[];
}

export function Axis({ y, yTicks = 4, yFormat = String, x, xLabels }: AxisProps) {
  const { xScale, yScale, plot, chrome, colors } = useChart();
  const left = plot.x;
  const right = plot.x + plot.w;
  const bottom = plot.y + plot.h;
  return (
    <g>
      {y && yScale &&
        niceTicks(yScale.domain[0], yScale.domain[1], yTicks).map((v) => {
          const py = yScale(v);
          return (
            <g key={`y${v}`}>
              <line
                x1={left}
                x2={right}
                y1={py}
                y2={py}
                stroke={chrome.gridline}
                strokeWidth={1}
                vectorEffect="non-scaling-stroke"
              />
              <text
                x={left - 6}
                y={py}
                textAnchor="end"
                dominantBaseline="central"
                fontSize={10}
                fill={colors.text.mute}
                style={{ fontVariantNumeric: "tabular-nums" }}
              >
                {yFormat(v)}
              </text>
            </g>
          );
        })}
      {x && (
        <line
          x1={left}
          x2={right}
          y1={bottom}
          y2={bottom}
          stroke={chrome.baseline}
          strokeWidth={1}
          vectorEffect="non-scaling-stroke"
        />
      )}
      {x && xLabels && xScale &&
        xLabels.map((label, i) => (
          <text
            key={`x${i}`}
            x={xScale(i)}
            y={bottom + 14}
            textAnchor="middle"
            fontSize={10}
            fill={colors.text.mute}
          >
            {label}
          </text>
        ))}
    </g>
  );
}
