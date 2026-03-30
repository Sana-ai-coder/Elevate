"""Topic-based MCQ generator using local LLM + web context."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import json
import random
import re
import time
from typing import Dict, List, Optional

from config import (
    CPU_LLM_MAX_ATTEMPTS,
    CPU_LLM_DISABLE_THRESHOLD,
    CPU_LLM_MAX_NEW_TOKENS,
    CPU_LLM_MAX_TARGET,
    ENABLE_TEMPLATE_FALLBACK,
    FACT_SENTENCE_MIN_CHARS,
    LLM_BATCH_SIZE,
    LLM_GENERATE_MAX_TIME_SECONDS,
    LLM_MAX_ATTEMPTS,
    LLM_ONLY_MODE,
    LLM_TOTAL_TIME_BUDGET_SECONDS,
    MAX_LLM_QUESTIONS_PER_REQUEST,
)
from models.llm import get_llm_model
from web_context import build_topic_web_context


STOPWORDS = {
    "the", "and", "that", "with", "from", "into", "about", "this", "these", "those", "which",
    "where", "when", "what", "while", "than", "then", "have", "has", "had", "been", "being",
    "over", "under", "their", "there", "after", "before", "because", "between", "through", "during",
    "using", "used", "such", "also", "into", "onto", "were", "was", "are", "is", "for", "off",
    "you", "your", "they", "them", "its", "our", "ours", "his", "her", "hers", "who", "why",
}


class MCQGenerator:
    def __init__(self) -> None:
        self.llm = get_llm_model()
        self._result_cache: OrderedDict[tuple, List[Dict]] = OrderedDict()
        self._cache_limit = 128
        self.last_generation_meta: Dict = {}

    def _cache_key(
        self,
        *,
        topic: str,
        num_questions: int,
        difficulty: str,
        subject: str,
        grade: str,
        seed: Optional[int],
        llm_only: bool,
    ) -> tuple:
        return (
            str(topic or "").strip().lower(),
            int(num_questions),
            str(difficulty or "medium").strip().lower(),
            str(subject or "science").strip().lower(),
            str(grade or "high").strip().lower(),
            int(seed) if seed is not None else None,
            bool(llm_only),
        )

    def _get_cached(self, key: tuple) -> Optional[List[Dict]]:
        rows = self._result_cache.get(key)
        if rows is None:
            return None
        self._result_cache.move_to_end(key)
        return deepcopy(rows)

    def _set_cached(self, key: tuple, rows: List[Dict]) -> None:
        self._result_cache[key] = deepcopy(rows)
        self._result_cache.move_to_end(key)
        while len(self._result_cache) > self._cache_limit:
            self._result_cache.popitem(last=False)

    def _is_cpu_runtime(self) -> bool:
        return str(getattr(self.llm, "device", "cpu")).lower() != "cuda"

    def generate_from_topic(
        self,
        topic: str,
        num_questions: int = 5,
        difficulty: str = "medium",
        subject: str = "science",
        grade: str = "high",
        seed: Optional[int] = None,
        llm_only: Optional[bool] = None,
    ) -> List[Dict]:
        safe_topic = str(topic or "").strip() or "general science"
        requested_count = int(max(1, min(num_questions, 50)))
        effective_llm_only = LLM_ONLY_MODE if llm_only is None else bool(llm_only)
        rng = random.Random(seed)

        cache_key = self._cache_key(
            topic=safe_topic,
            num_questions=requested_count,
            difficulty=difficulty,
            subject=subject,
            grade=grade,
            seed=seed,
            llm_only=effective_llm_only,
        )

        cached_rows = self._get_cached(cache_key)
        if cached_rows is not None:
            final_cached = cached_rows[:requested_count]
            self.last_generation_meta = {
                "requested": requested_count,
                "produced": len(final_cached),
                "llm_count": len([q for q in final_cached if q.get("source") == "llm"]),
                "template_count": len([q for q in final_cached if q.get("source") == "template"]),
                "llm_only_mode": effective_llm_only,
                "fallback_used": any(q.get("source") == "template" for q in final_cached),
                "cache_hit": True,
            }
            return final_cached

        context = build_topic_web_context(safe_topic)
        facts = self._extract_facts(context, safe_topic)
        is_cpu = self._is_cpu_runtime()

        if effective_llm_only:
            llm_target = requested_count
        else:
            if requested_count >= 20:
                llm_target = min(4, MAX_LLM_QUESTIONS_PER_REQUEST)
            elif requested_count >= 10:
                llm_target = min(6, MAX_LLM_QUESTIONS_PER_REQUEST)
            else:
                llm_target = min(requested_count, MAX_LLM_QUESTIONS_PER_REQUEST)

            if is_cpu:
                if requested_count >= max(1, CPU_LLM_DISABLE_THRESHOLD):
                    llm_target = 0
                else:
                    llm_target = min(llm_target, max(0, CPU_LLM_MAX_TARGET), requested_count)

        llm_rows = self._generate_llm_mcqs(
            topic=safe_topic,
            subject=subject,
            grade=grade,
            difficulty=difficulty,
            requested_count=llm_target,
            facts=facts,
            strict_llm=effective_llm_only,
        )

        accepted = [row for row in llm_rows if self._is_quality_question(row)]
        accepted = self._dedupe_questions(accepted)

        if effective_llm_only:
            final_rows = accepted[:requested_count]
            self.last_generation_meta = {
                "requested": requested_count,
                "produced": len(final_rows),
                "llm_count": len(final_rows),
                "template_count": 0,
                "facts_count": len(facts),
                "llm_only_mode": True,
                "fallback_used": False,
                "llm_shortfall": max(0, requested_count - len(final_rows)),
                "cache_hit": False,
            }
            self._set_cached(cache_key, final_rows)
            return final_rows

        template_rows: List[Dict] = []
        if ENABLE_TEMPLATE_FALLBACK and len(accepted) < requested_count:
            template_rows = self._generate_fact_based_mcqs(
                facts=facts,
                topic=safe_topic,
                difficulty=difficulty,
                needed=requested_count - len(accepted),
                rng=rng,
            )
            accepted = self._dedupe_questions(accepted + template_rows)

        if ENABLE_TEMPLATE_FALLBACK and len(accepted) < requested_count:
            generic_rows = self._generate_generic_topic_mcqs(
                topic=safe_topic,
                difficulty=difficulty,
                needed=requested_count - len(accepted),
                rng=rng,
            )
            accepted = self._dedupe_questions(accepted + generic_rows)

        final_rows = accepted[:requested_count]

        self.last_generation_meta = {
            "requested": requested_count,
            "produced": len(final_rows),
            "llm_count": len([q for q in final_rows if q.get("source") == "llm"]),
            "template_count": len([q for q in final_rows if q.get("source") == "template"]),
            "facts_count": len(facts),
            "llm_only_mode": False,
            "fallback_used": len([q for q in final_rows if q.get("source") == "template"]) > 0,
            "cache_hit": False,
        }

        self._set_cached(cache_key, final_rows)
        return final_rows

    def _create_llm_prompt(
        self,
        *,
        topic: str,
        subject: str,
        grade: str,
        num_questions: int,
        difficulty: str,
        facts: List[str],
        existing_questions: Optional[List[str]] = None,
    ) -> str:
        fact_lines = "\n".join(f"- {fact}" for fact in facts[:18])
        avoid_repeat = ""
        if existing_questions:
            history = "\n".join(f"- {q}" for q in existing_questions[:12] if q)
            if history:
                avoid_repeat = (
                    "Do not repeat or paraphrase the following already generated questions:\n"
                    f"{history}\n\n"
                )

        return (
            "Generate factual multiple-choice questions from only the provided facts.\n"
            "Return ONLY valid JSON as an array of objects with keys: question, options, answer, explanation.\n"
            "Use ONLY option keys A, B, C, D. Never output E or F options.\n"
            "If you cannot satisfy the rules, output [] exactly.\n"
            "Rules:\n"
            "1) options must be exactly 4 unique strings.\n"
            "2) answer must be one of A, B, C, D and must match options.\n"
            "3) Avoid vague or conversational wording.\n"
            "4) Questions must be factual and specific.\n"
            "5) Explanations must be short and evidence-oriented.\n\n"
            "Schema example:\n"
            "[\n"
            "  {\n"
            "    \"question\": \"Which statement about Newton's second law is correct?\",\n"
            "    \"options\": {\"A\": \"Force equals mass times acceleration\", \"B\": \"Energy equals mass times acceleration\", \"C\": \"Momentum equals force times velocity\", \"D\": \"Power equals force times distance\"},\n"
            "    \"answer\": \"A\",\n"
            "    \"explanation\": \"Newton's second law is F = m * a.\"\n"
            "  }\n"
            "]\n\n"
            f"Subject: {subject}\n"
            f"Grade: {grade}\n"
            f"Difficulty: {difficulty}\n"
            f"Topic: {topic}\n"
            f"Required Questions: {num_questions}\n\n"
            f"{avoid_repeat}"
            "Facts:\n"
            f"{fact_lines}\n"
        )

    def _create_llm_plain_prompt(
        self,
        *,
        topic: str,
        subject: str,
        grade: str,
        difficulty: str,
        facts: List[str],
        existing_questions: Optional[List[str]] = None,
    ) -> str:
        fact_lines = "\n".join(f"- {fact}" for fact in facts[:12])
        avoid_repeat = ""
        if existing_questions:
            history = "\n".join(f"- {q}" for q in existing_questions[:8] if q)
            if history:
                avoid_repeat = f"Do not repeat these questions:\n{history}\n\n"

        return (
            "Create exactly one factual MCQ using only the facts below.\n"
            "Output ONLY this exact format:\n"
            "Question: <text ending with ?>\n"
            "A) <option>\n"
            "B) <option>\n"
            "C) <option>\n"
            "D) <option>\n"
            "Answer: <A|B|C|D>\n"
            "Explanation: <short factual reason>\n"
            "Do not output JSON. Do not output E or F options.\n\n"
            f"Subject: {subject}\n"
            f"Grade: {grade}\n"
            f"Difficulty: {difficulty}\n"
            f"Topic: {topic}\n\n"
            f"{avoid_repeat}"
            "Facts:\n"
            f"{fact_lines}\n"
        )

    def _extract_json_block(self, response: str) -> str:
        text = str(response or "").strip()
        if not text:
            return ""

        fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
        if fence:
            text = fence.group(1).strip()

        if text.startswith("[") and text.endswith("]"):
            return text
        if text.startswith("{") and text.endswith("}"):
            return text

        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            return text[start : end + 1]

        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return text[start : end + 1]

        return ""

    def _normalize_candidate(self, item: Dict, difficulty: str, topic: str, source: str) -> Optional[Dict]:
        if not isinstance(item, dict):
            return None

        question = str(item.get("question") or item.get("text") or "").strip()
        if not question:
            return None
        if not question.endswith("?"):
            question = f"{question.rstrip('.')}?"

        raw_options = item.get("options")
        options: List[str] = []
        if isinstance(raw_options, list):
            options = [str(opt).strip() for opt in raw_options if str(opt).strip()]
        elif isinstance(raw_options, dict):
            for key in ["A", "B", "C", "D", "a", "b", "c", "d"]:
                value = raw_options.get(key)
                if value is None:
                    continue
                text = str(value).strip()
                if text:
                    options.append(text)

        normalized_options: List[str] = []
        seen_opts = set()
        for opt in options:
            key = " ".join(opt.lower().split())
            if key in seen_opts:
                continue
            seen_opts.add(key)
            normalized_options.append(opt)
            if len(normalized_options) == 4:
                break

        if len(normalized_options) != 4:
            return None

        answer = str(item.get("answer") or item.get("correct_answer") or "").strip().upper()
        if answer not in {"A", "B", "C", "D"}:
            try:
                idx = int(item.get("correct_index"))
                if 0 <= idx < 4:
                    answer = ["A", "B", "C", "D"][idx]
            except Exception:
                return None

        if answer not in {"A", "B", "C", "D"}:
            return None

        correct_index = ["A", "B", "C", "D"].index(answer)
        explanation = str(item.get("explanation") or "Based on documented facts.").strip()

        return {
            "question": question,
            "options": normalized_options,
            "correct_answer": answer,
            "correct_index": correct_index,
            "explanation": explanation,
            "hint": f"Choose the option that best matches verified facts about {topic}.",
            "difficulty": difficulty,
            "topic": topic,
            "source": source,
        }

    def _parse_legacy_question_block(self, block: str, difficulty: str, topic: str) -> Optional[Dict]:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            return None

        question = re.sub(
            r"^(?:Question(?:\s*\d+)?[:.)-]?|Q\d+[:.)-]|\d+[.)-])\s*",
            "",
            lines[0],
            flags=re.IGNORECASE,
        ).strip()
        if not question:
            return None

        options_by_letter: Dict[str, str] = {}
        answer_letter = ""
        explanation = ""

        for line in lines[1:]:
            option_match = re.match(
                r"^[\-*\s]*[\(\[]?\s*([A-F])\s*[\)\].:\-]?\s+(.+)$",
                line,
                flags=re.IGNORECASE,
            )
            if option_match:
                letter = option_match.group(1).upper()
                value = option_match.group(2).strip()
                if value:
                    options_by_letter[letter] = value
                continue

            if "answer" in line.lower():
                found = re.search(r"[\(\[]?\s*([A-D])\s*[\)\]]?", line, flags=re.IGNORECASE)
                if found:
                    answer_letter = found.group(1).upper()
                continue

            if line.lower().startswith("explanation"):
                explanation = re.sub(r"^explanation[:\s-]*", "", line, flags=re.IGNORECASE).strip()

        ordered_letters = ["A", "B", "C", "D"]
        options = [options_by_letter[letter] for letter in ordered_letters if letter in options_by_letter]
        if len(options) < 4:
            return None

        if answer_letter not in ordered_letters:
            return None

        return self._normalize_candidate(
            {
                "question": question,
                "options": options[:4],
                "answer": answer_letter,
                "explanation": explanation or "Based on the retrieved topic context.",
            },
            difficulty,
            topic,
            source="llm",
        )

    def _parse_llm_output(self, response: str, difficulty: str, topic: str) -> List[Dict]:
        json_block = self._extract_json_block(response)
        candidates: List[Dict] = []

        if json_block:
            try:
                parsed = json.loads(json_block)
                if isinstance(parsed, dict):
                    rows = parsed.get("questions") or parsed.get("mcqs") or []
                elif isinstance(parsed, list):
                    rows = parsed
                else:
                    rows = []

                if isinstance(rows, list):
                    for item in rows:
                        row = self._normalize_candidate(item, difficulty, topic, source="llm")
                        if row:
                            candidates.append(row)
            except Exception:
                pass

        if candidates:
            return candidates

        blocks = re.split(
            r"\n(?=\s*(?:Question(?:\s*\d+)?[:.)-]?|Q\d+[:.)-]|\d+[.)-]))",
            str(response or ""),
            flags=re.IGNORECASE,
        )
        for raw_block in blocks:
            row = self._parse_legacy_question_block(raw_block.strip(), difficulty, topic)
            if row:
                candidates.append(row)

        if candidates:
            return candidates

        question_match = re.search(r"(?im)^\s*Question\s*:\s*(.+)$", str(response or ""))
        if question_match:
            question_text = question_match.group(1).strip()
            bullet_lines = re.findall(r"(?im)^\s*[-*]\s+(.+)$", str(response or ""))

            options: List[str] = []
            seen = set()
            for line in bullet_lines:
                opt = re.sub(r"\s+", " ", str(line or "")).strip().rstrip(".")
                key = opt.lower()
                if not opt or key in seen:
                    continue
                seen.add(key)
                options.append(opt)

            fallback_opts = [
                f"It is unrelated to established physics principles in {topic}.",
                f"It denies core evidence-based findings about {topic}.",
                f"It contradicts standard scientific reasoning about {topic}.",
                f"It excludes measured effects commonly studied in {topic}.",
            ]
            for opt in fallback_opts:
                key = opt.lower()
                if key in seen:
                    continue
                seen.add(key)
                options.append(opt)
                if len(options) >= 4:
                    break

            if len(options) >= 4:
                row = self._normalize_candidate(
                    {
                        "question": question_text,
                        "options": options[:4],
                        "answer": "A",
                        "explanation": "Derived from the model's bullet-point answer.",
                    },
                    difficulty,
                    topic,
                    source="llm",
                )
                if row:
                    candidates.append(row)

        return candidates

    def _generate_llm_mcqs(
        self,
        *,
        topic: str,
        subject: str,
        grade: str,
        difficulty: str,
        requested_count: int,
        facts: List[str],
        strict_llm: bool = False,
    ) -> List[Dict]:
        if requested_count <= 0:
            return []

        is_cpu = self._is_cpu_runtime()
        collected: List[Dict] = []
        attempts = 0
        max_attempts = max(2, requested_count)
        if strict_llm:
            max_attempts = max(max_attempts, max(2, LLM_MAX_ATTEMPTS))
        elif is_cpu:
            max_attempts = min(max_attempts, max(1, CPU_LLM_MAX_ATTEMPTS))

        started = time.perf_counter()
        consecutive_empty = 0

        while len(collected) < requested_count and attempts < max_attempts:
            if strict_llm and (time.perf_counter() - started) >= max(5.0, LLM_TOTAL_TIME_BUDGET_SECONDS):
                break

            remaining = requested_count - len(collected)
            if is_cpu and strict_llm:
                batch_size = min(1, remaining)
            elif is_cpu:
                batch_size = min(1, remaining)
            else:
                batch_size = min(LLM_BATCH_SIZE, remaining)

            existing_questions = [str(row.get("question") or "") for row in collected if row.get("question")]
            if strict_llm and is_cpu:
                prompt = self._create_llm_plain_prompt(
                    topic=topic,
                    subject=subject,
                    grade=grade,
                    difficulty=difficulty,
                    facts=facts,
                    existing_questions=existing_questions,
                )
            else:
                prompt = self._create_llm_prompt(
                    topic=topic,
                    subject=subject,
                    grade=grade,
                    num_questions=batch_size,
                    difficulty=difficulty,
                    facts=facts,
                    existing_questions=existing_questions,
                )

            token_cap = min(max(160, batch_size * 85), 360)
            if is_cpu:
                cpu_token_cap = max(32, CPU_LLM_MAX_NEW_TOKENS)
                if strict_llm:
                    cpu_token_cap = max(cpu_token_cap, 180)
                token_cap = min(token_cap, cpu_token_cap)

            temperature = 0.05 if attempts == 0 else 0.12
            top_p = 0.95 if attempts == 0 else 0.98
            max_time = LLM_GENERATE_MAX_TIME_SECONDS
            if strict_llm and is_cpu:
                max_time = max(max_time, 60.0)

            response = self.llm.generate(
                prompt=prompt,
                max_new_tokens=token_cap,
                temperature=temperature,
                top_p=top_p,
                max_time=max_time,
            )
            parsed = self._parse_llm_output(response, difficulty, topic)
            if parsed:
                collected.extend(parsed)
                collected = self._dedupe_questions(collected)
                consecutive_empty = 0
            else:
                consecutive_empty += 1

            if strict_llm and consecutive_empty >= 3 and len(collected) > 0:
                break

            attempts += 1

        return collected[:requested_count]

    def _extract_facts(self, context: str, topic: str) -> List[str]:
        text = str(context or "").replace("\r", " ").strip()
        text = re.sub(r"(?im)^topic:\s*[^\n]*", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return [f"{topic} is an important STEM topic with measurable real-world impact."]

        topic_tokens = {tok.lower() for tok in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", topic)}
        sentence_candidates = re.split(r"(?<=[.!?])\s+", text)

        facts: List[str] = []
        seen = set()
        for sentence in sentence_candidates:
            cleaned = re.sub(r"\s+", " ", sentence).strip(" -\t\n")
            if len(cleaned) < FACT_SENTENCE_MIN_CHARS or len(cleaned) > 260:
                continue

            lowered = cleaned.lower()
            if topic_tokens and not any(tok in lowered for tok in topic_tokens):
                if not any(v in lowered for v in [" is ", " are ", " was ", " were ", " include", " uses", " enables"]):
                    continue

            key = " ".join(cleaned.lower().split())
            if key in seen:
                continue
            seen.add(key)
            facts.append(cleaned.rstrip("."))
            if len(facts) >= 96:
                break

        if not facts:
            facts.append(f"{topic} is a domain with established principles and practical applications.")
        return facts

    def _extract_terms(self, facts: List[str], topic: str) -> List[str]:
        term_counts: Dict[str, int] = {}
        seed_terms = re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", topic or "")
        for term in seed_terms:
            key = term.lower()
            if key not in STOPWORDS:
                term_counts[term] = term_counts.get(term, 0) + 3

        for fact in facts:
            for token in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", fact):
                lowered = token.lower()
                if lowered in STOPWORDS:
                    continue
                term_counts[token] = term_counts.get(token, 0) + 1

        ranked = sorted(term_counts.items(), key=lambda x: (-x[1], x[0].lower()))
        return [token for token, _ in ranked[:128]]

    def _mutate_fact_sentence(self, fact: str, terms: List[str], rng: random.Random) -> str:
        candidate = fact

        numbers = re.findall(r"\b\d+(?:\.\d+)?\b", candidate)
        if numbers:
            n = numbers[0]
            try:
                delta = rng.choice([-2, -1, 1, 2, 5])
                replacement = str(max(1, int(float(n)) + delta))
                candidate = re.sub(re.escape(n), replacement, candidate, count=1)
                return candidate
            except Exception:
                pass

        words = re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", candidate)
        if words and terms:
            source_word = rng.choice(words)
            replacement = rng.choice(terms)
            if source_word.lower() != replacement.lower():
                candidate = re.sub(rf"\b{re.escape(source_word)}\b", replacement, candidate, count=1)
                return candidate

        polarity_swaps = {
            "increase": "decrease",
            "decrease": "increase",
            "higher": "lower",
            "lower": "higher",
            "more": "less",
            "less": "more",
            "enable": "prevent",
            "improves": "worsens",
        }
        for src, dst in polarity_swaps.items():
            if re.search(rf"\b{src}\b", candidate, flags=re.IGNORECASE):
                return re.sub(rf"\b{src}\b", dst, candidate, count=1, flags=re.IGNORECASE)

        return f"It is unrelated to {rng.choice(terms) if terms else 'the core topic'}"

    def _fact_to_question(self, fact: str, topic: str, style_index: int) -> str:
        topic_name = topic.replace("_", " ")
        fact_stub = re.sub(r"\s+", " ", str(fact or "")).strip().rstrip(".")
        if len(fact_stub) > 90:
            fact_stub = f"{fact_stub[:90].rsplit(' ', 1)[0]}..."

        if style_index % 3 == 0:
            return f"Based on this context fact about {topic_name}, which option is accurate: \"{fact_stub}\"?"
        if style_index % 3 == 1:
            return f"According to the provided context on {topic_name}, which claim is correct?"
        return f"Which option best matches documented facts on {topic_name}?"

    def _generate_fact_based_mcqs(
        self,
        *,
        facts: List[str],
        topic: str,
        difficulty: str,
        needed: int,
        rng: random.Random,
    ) -> List[Dict]:
        if needed <= 0:
            return []

        terms = self._extract_terms(facts, topic)
        rows: List[Dict] = []

        for idx in range(needed * 3):
            if len(rows) >= needed:
                break

            fact = facts[idx % len(facts)]
            question = self._fact_to_question(fact, topic, idx)
            correct_option = fact.rstrip(".")

            distractors: List[str] = []
            attempts = 0
            while len(distractors) < 3 and attempts < 12:
                candidate = self._mutate_fact_sentence(fact, terms, rng).strip().rstrip(".")
                attempts += 1
                if not candidate:
                    continue
                if candidate.lower() == correct_option.lower():
                    continue
                if any(candidate.lower() == d.lower() for d in distractors):
                    continue
                distractors.append(candidate)

            if len(distractors) < 3:
                continue

            options = [correct_option] + distractors
            rng.shuffle(options)
            correct_index = options.index(correct_option)
            answer_letter = ["A", "B", "C", "D"][correct_index]

            row = self._normalize_candidate(
                {
                    "question": question,
                    "options": options,
                    "answer": answer_letter,
                    "explanation": f"The correct option directly reflects the documented fact: {correct_option}.",
                },
                difficulty,
                topic,
                source="template",
            )
            if row and self._is_quality_question(row):
                rows.append(row)

        return self._dedupe_questions(rows)[:needed]

    def _generate_generic_topic_mcqs(
        self,
        *,
        topic: str,
        difficulty: str,
        needed: int,
        rng: random.Random,
    ) -> List[Dict]:
        if needed <= 0:
            return []

        base_terms = [tok for tok in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", topic) if tok.lower() not in STOPWORDS]
        if not base_terms:
            base_terms = ["concept", "principle", "application", "method"]

        rows: List[Dict] = []
        for idx in range(needed):
            key = base_terms[idx % len(base_terms)]
            correct = f"It involves established principles of {key} in {topic.replace('_', ' ')}"
            options = [
                correct,
                f"It excludes all measurable outcomes related to {key}",
                f"It is unrelated to scientific reasoning and data about {key}",
                f"It avoids practical applications of {key}",
            ]
            rng.shuffle(options)
            correct_index = options.index(correct)
            answer_letter = ["A", "B", "C", "D"][correct_index]
            if idx % 3 == 0:
                question_text = f"Which statement best describes the role of {key} in {topic.replace('_', ' ')}?"
            elif idx % 3 == 1:
                question_text = f"In {topic.replace('_', ' ')}, which claim about {key} is most accurate?"
            else:
                question_text = f"Which option correctly reflects a core {key} principle in {topic.replace('_', ' ')}?"

            row = self._normalize_candidate(
                {
                    "question": question_text,
                    "options": options,
                    "answer": answer_letter,
                    "explanation": "The correct option aligns with foundational facts used in STEM learning.",
                },
                difficulty,
                topic,
                source="template",
            )
            if row:
                rows.append(row)

        return rows[:needed]

    def _is_quality_question(self, row: Dict) -> bool:
        question = str(row.get("question") or "").strip()
        if len(question) < 18:
            return False
        if "____" in question.lower() or "fill in the blank" in question.lower():
            return False

        options = row.get("options") if isinstance(row.get("options"), list) else []
        if len(options) != 4:
            return False

        normalized = [" ".join(str(opt).lower().split()) for opt in options]
        if len(set(normalized)) != 4:
            return False

        weak_opts = {"none of the above", "all of the above", "none of these"}
        if any(opt in weak_opts for opt in normalized):
            return False

        if row.get("correct_answer") not in {"A", "B", "C", "D"}:
            return False

        return True

    def _dedupe_questions(self, rows: List[Dict]) -> List[Dict]:
        unique: List[Dict] = []
        seen = set()
        for row in rows:
            question_key = " ".join(str(row.get("question", "")).lower().split())
            options = row.get("options") if isinstance(row.get("options"), list) else []
            options_key = "|".join(sorted(" ".join(str(opt).lower().split()) for opt in options))
            key = f"{question_key}|{options_key}"
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(row)
        return unique


_mcq_generator: MCQGenerator | None = None


def get_mcq_generator() -> MCQGenerator:
    global _mcq_generator
    if _mcq_generator is None:
        _mcq_generator = MCQGenerator()
    return _mcq_generator
