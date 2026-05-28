import { FormEvent, useState } from "react";
import { ArrowUp, Plus } from "lucide-react";

type PromptBoxProps = {
  placeholder?: string;
  disabled?: boolean;
  onSubmit: (value: string) => void;
};

export function PromptBox({ placeholder, disabled, onSubmit }: PromptBoxProps) {
  const [value, setValue] = useState("");

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const text = value.trim();
    if (!text || disabled) return;
    onSubmit(text);
    setValue("");
  }

  return (
    <form className="prompt-box" onSubmit={handleSubmit}>
      <button className="icon-button" type="button" aria-label="add context" disabled={disabled}>
        <Plus size={20} />
      </button>
      <input
        value={value}
        disabled={disabled}
        onChange={(event) => setValue(event.target.value)}
        placeholder={placeholder ?? "输入竞品分析需求，或继续询问当前报告"}
      />
      <button className="send-button" type="submit" aria-label="send" disabled={disabled || !value.trim()}>
        <ArrowUp size={18} />
      </button>
    </form>
  );
}
