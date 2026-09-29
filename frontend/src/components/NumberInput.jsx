import { forwardRef, useRef } from "react";

const NumberInput = forwardRef(function NumberInput({ stepperLabel = "value", stepperStep, onChange, value, defaultValue, min, max, step, disabled, readOnly, className = "", ...props }, forwardedRef) {
  const inputRef = useRef(null);
  const current = value ?? defaultValue;
  const currentNumber = current === "" || current == null ? null : Number(current);
  const minimum = min === "" || min == null ? null : Number(min);
  const maximum = max === "" || max == null ? null : Number(max);
  const atMin = currentNumber != null && minimum != null && currentNumber <= minimum;
  const atMax = currentNumber != null && maximum != null && currentNumber >= maximum;

  function setRef(node) {
    inputRef.current = node;
    if (typeof forwardedRef === "function") forwardedRef(node);
    else if (forwardedRef) forwardedRef.current = node;
  }

  function changeBy(direction) {
    const input = inputRef.current;
    if (!input || disabled || readOnly) return;
    const previous = input.value;
    const previousNumber = Number(previous);
    const nativeStep = step === "any" ? 1 : Number(step) > 0 ? Number(step) : 1;
    const baseStep = Number(stepperStep) > 0 ? Number(stepperStep) : nativeStep;
    const jump = Math.max(baseStep, (Number.isFinite(previousNumber) ? Math.abs(previousNumber) : 0) * 0.05);
    if (step === "any") {
      const nextValue = previous === "" ? direction * jump : previousNumber + direction * jump;
      if (!Number.isFinite(nextValue)) return;
      const next = Math.min(maximum ?? Infinity, Math.max(minimum ?? -Infinity, nextValue));
      input.value = String(Number(next.toPrecision(12)));
    } else if (previous === "" && stepperStep) {
      input.value = String(Math.min(maximum ?? Infinity, Math.max(minimum ?? -Infinity, direction * jump)));
    } else {
      try {
        const steps = Math.max(1, Math.round(jump / nativeStep));
        if (direction > 0) input.stepUp(steps);
        else input.stepDown(steps);
      } catch {
        return;
      }
    }
    if (input.value !== previous) onChange?.({ target: input, currentTarget: input });
    input.focus({ preventScroll: true });
  }

  return <span className={`number-input ${className}`}>
    <input {...props} ref={setRef} type="number" aria-label={props["aria-label"] ?? stepperLabel} value={value} defaultValue={defaultValue} min={min} max={max} step={step} disabled={disabled} readOnly={readOnly} onChange={onChange} />
    <span className="number-input-controls">
      <button type="button" aria-label={`Increase ${stepperLabel}`} disabled={disabled || readOnly || atMax} onClick={() => changeBy(1)}><span aria-hidden="true" className="number-input-chevron up" /></button>
      <button type="button" aria-label={`Decrease ${stepperLabel}`} disabled={disabled || readOnly || atMin} onClick={() => changeBy(-1)}><span aria-hidden="true" className="number-input-chevron down" /></button>
    </span>
  </span>;
});

export default NumberInput;
