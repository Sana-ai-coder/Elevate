"""Topic-based MCQ generator using local LLM + web context."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import json
import random
import re
import threading
import time
from typing import Dict, List, Optional

import torch

from config import (
    CPU_LLM_MAX_ATTEMPTS,
    CPU_LLM_MAX_NEW_TOKENS,
    FACT_SENTENCE_MIN_CHARS,
    LLM_BATCH_SIZE,
    LLM_GENERATE_MAX_TIME_SECONDS,
    LLM_MAX_ATTEMPTS,
    LLM_TOTAL_TIME_BUDGET_SECONDS,
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
        self.llm = None
        self._llm_lock = threading.Lock()
        self._result_cache: OrderedDict[tuple, List[Dict]] = OrderedDict()
        self._cache_limit = 128
        self.last_generation_meta: Dict = {}

    def ensure_model_loaded(self):
        if self.llm is not None:
            return self.llm
        with self._llm_lock:
            if self.llm is None:
                self.llm = get_llm_model()
        return self.llm

    def is_model_loaded(self) -> bool:
        return self.llm is not None

    def _cache_key(
        self,
        *,
        topic: str,
        num_questions: int,
        difficulty: str,
        subject: str,
        grade: str,
        seed: Optional[int],
        test_title: str,
        test_description: str,
    ) -> tuple:
        return (
            str(topic or "").strip().lower(),
            int(num_questions),
            str(difficulty or "medium").strip().lower(),
            str(subject or "science").strip().lower(),
            str(grade or "high").strip().lower(),
            int(seed) if seed is not None else None,
            str(test_title or "").strip().lower()[:160],
            str(test_description or "").strip().lower()[:320],
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
        if self.llm is not None:
            return str(getattr(self.llm, "device", "cpu")).lower() != "cuda"
        return not torch.cuda.is_available()

    def generate_from_topic(
        self,
        topic: str,
        num_questions: int = 5,
        difficulty: str = "medium",
        subject: str = "science",
        grade: str = "high",
        seed: Optional[int] = None,
        test_title: Optional[str] = None,
        test_description: Optional[str] = None,
    ) -> List[Dict]:
        safe_topic = str(topic or "").strip()
        safe_test_title = str(test_title or "").strip()
        safe_test_description = str(test_description or "").strip()
        if not safe_topic:
            safe_topic = " ".join(part for part in [safe_test_title, safe_test_description] if part).strip() or "general science"

        requested_count = int(max(1, min(num_questions, 50)))
        cache_key = self._cache_key(
            topic=safe_topic, num_questions=requested_count, difficulty=difficulty,
            subject=subject, grade=grade, seed=seed, test_title=safe_test_title, test_description=safe_test_description,
        )

        cached_rows = self._get_cached(cache_key)
        if cached_rows is not None:
            return cached_rows[:requested_count]

        context = build_topic_web_context(safe_topic)
        facts = self._extract_facts(context, safe_topic)

        # 100% LLM Generation. No template fallbacks.
        final_rows = self._generate_llm_mcqs(
            topic=safe_topic,
            subject=subject,
            grade=grade,
            difficulty=difficulty,
            requested_count=requested_count,
            facts=facts,
            test_title=safe_test_title,
            test_description=safe_test_description,
        )

        self.last_generation_meta = {
            "requested": requested_count,
            "produced": len(final_rows),
            "llm_count": len(final_rows),
            "template_count": 0, # Hardcoded to 0 because we removed templates
            "facts_count": len(facts),
            "strict_llm_mode": True,
            "fallback_used": False, # Never uses fallbacks anymore
            "llm_shortfall": max(0, requested_count - len(final_rows)),
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
        test_title: str = "",
        test_description: str = "",
        existing_questions: Optional[List[str]] = None,
    ) -> str:
        # Feed exactly 10 facts so the model reads quickly and responds instantly
        fact_lines = "\n".join(f"- {fact}" for fact in facts[:10])
        return (
            f"Generate EXACTLY {num_questions} multiple-choice questions about '{topic}'.\n"
            "Use ONLY the facts below. DO NOT output JSON. Use exactly this text format for each question:\n\n"
            "Question: <the question>\n"
            "A) <option 1>\n"
            "B) <option 2>\n"
            "C) <option 3>\n"
            "D) <option 4>\n"
            "Answer: <A, B, C, or D>\n"
            "Explanation: <one short sentence>\n\n"
            "Facts:\n"
            f"{fact_lines}\n"
        )

    def _generate_llm_mcqs(
        self,
        *,
        topic: str,
        subject: str,
        grade: str,
        difficulty: str,
        requested_count: int,
        facts: List[str],
        test_title: str = "",
        test_description: str = "",
    ) -> List[Dict]:
        llm = self.ensure_model_loaded()
        collected: List[Dict] = []
        attempts = 0
        started = time.perf_counter()

        # Try up to 3 times, but force a hard stop at 85 seconds so Render NEVER times out (504/502 error)
        while len(collected) < requested_count and attempts < 3:
            if (time.perf_counter() - started) >= 85.0:
                break

            remaining = requested_count - len(collected)
            prompt = self._create_llm_prompt(
                topic=topic, subject=subject, grade=grade, num_questions=remaining,
                difficulty=difficulty, facts=facts, test_title=test_title, test_description=test_description
            )

            response = llm.generate(
                prompt=prompt,
                max_new_tokens=1000, # Give it massive room to write everything
                temperature=0.1,
                top_p=0.95,
                max_time=45.0, # 45 seconds per attempt. Lightning fast for plain text.
            )
            
            parsed = self._parse_llm_output(response, difficulty, topic)
            if parsed:
                collected.extend(parsed)
                collected = self._dedupe_questions(collected)

            attempts += 1

        return collected[:requested_count]

    def _grade_guidance(self, grade: str) -> str:
        grade_key = str(grade or "").strip().lower()
        if grade_key == "elementary":
            return "Use concrete, simple wording and foundational concepts suitable for young learners."
        if grade_key == "middle":
            return "Use school-level academic wording and moderate reasoning steps suitable for middle school."
        if grade_key == "high":
            return "Use exam-style high-school rigor with clear conceptual and applied reasoning."
        if grade_key == "college":
            return "Use concise technical language and deeper conceptual reasoning expected in introductory college assessments."
        return "Match question wording and reasoning depth to the provided grade level."

    def _difficulty_guidance(self, difficulty: str) -> str:
        difficulty_key = str(difficulty or "").strip().lower()
        if difficulty_key == "easy":
            return "Prioritize direct concept checks and one-step reasoning."
        if difficulty_key == "medium":
            return "Use moderate multi-step reasoning and common exam-style distractors."
        if difficulty_key == "hard":
            return "Use deeper conceptual traps and higher-order reasoning while keeping one unambiguous correct answer."
        return "Align challenge level to the requested difficulty."

    def _test_context_block(self, test_title: str, test_description: str) -> str:
        title = str(test_title or "").strip()
        description = str(test_description or "").strip()
        if not title and not description:
            return ""

        parts = []
        if title:
            parts.append(f"Assessment Title: {title}")
        if description:
            parts.append(f"Assessment Description: {description}")
        return "\n".join(parts)


    def _create_llm_plain_prompt(
        self,
        *,
        topic: str,
        subject: str,
        grade: str,
        difficulty: str,
        facts: List[str],
        test_title: str = "",
        test_description: str = "",
        existing_questions: Optional[List[str]] = None,
    ) -> str:
        fact_lines = "\n".join(f"- {fact}" for fact in facts[:12])
        test_context = self._test_context_block(test_title, test_description)
        grade_guidance = self._grade_guidance(grade)
        difficulty_guidance = self._difficulty_guidance(difficulty)
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
            "Explanation: <one short sentence, max 20 words>\n"
            "All four options must be sensible and distinct.\n"
            "Do not output JSON. Do not output E or F options.\n\n"
            f"Subject: {subject}\n"
            f"Grade: {grade}\n"
            f"Difficulty: {difficulty}\n"
            f"Topic: {topic}\n\n"
            f"Grade Guidance: {grade_guidance}\n"
            f"Difficulty Guidance: {difficulty_guidance}\n"
            f"{test_context}\n\n"
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
        question = re.sub(r"\s+", " ", question).strip()

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
            clean_opt = re.sub(r"\s+", " ", str(opt or "")).strip().rstrip(".")
            clean_opt = re.sub(r"^[\(\[]?[A-Da-d][\)\].:\-]\s*", "", clean_opt).strip()
            if len(clean_opt) > 180:
                clean_opt = clean_opt[:177].rstrip() + "..."
            if not clean_opt:
                continue
            key = " ".join(clean_opt.lower().split())
            if key in seen_opts:
                continue
            seen_opts.add(key)
            normalized_options.append(clean_opt)
            if len(normalized_options) == 4:
                break

        if len(normalized_options) != 4:
            return None

        answer = ""
        correct_index: Optional[int] = None

        raw_answer = str(item.get("answer") or item.get("correct_answer") or "").strip()
        if raw_answer:
            normalized_answer = raw_answer.upper()
            if normalized_answer in {"A", "B", "C", "D"}:
                answer = normalized_answer
                correct_index = ["A", "B", "C", "D"].index(answer)
            else:
                letter_match = re.search(r"\b([A-D])\b", normalized_answer)
                if letter_match:
                    answer = letter_match.group(1)
                    correct_index = ["A", "B", "C", "D"].index(answer)

        if correct_index is None:
            try:
                idx = int(item.get("correct_index"))
                if 0 <= idx < 4:
                    correct_index = idx
                elif 1 <= idx <= 4:
                    # Some model outputs use 1-based indexing.
                    correct_index = idx - 1
            except Exception:
                correct_index = None

        if correct_index is None and raw_answer:
            normalized_answer_text = " ".join(raw_answer.strip().lower().split())
            for idx, option_text in enumerate(normalized_options):
                normalized_option_text = " ".join(str(option_text).strip().lower().split())
                if normalized_answer_text == normalized_option_text:
                    correct_index = idx
                    break

        if correct_index is None:
            return None

        answer = ["A", "B", "C", "D"][correct_index]
        raw_explanation = str(item.get("explanation") or "Based on documented facts.").strip()
        raw_explanation = re.sub(r"\s+", " ", raw_explanation)
        first_sentence = re.split(r"(?<=[.!?])\s+", raw_explanation, maxsplit=1)[0].strip()
        explanation = first_sentence if first_sentence else "Based on documented facts."
        if len(explanation) > 180:
            explanation = explanation[:177].rstrip() + "..."

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

        return candidates

    

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
        if len(question) < 12:
            return False
        if len(question.split()) < 4:
            return False
        if "____" in question.lower() or "fill in the blank" in question.lower():
            return False

        options = row.get("options") if isinstance(row.get("options"), list) else []
        if len(options) != 4:
            return False

        if any(len(str(opt).strip()) < 2 or len(str(opt).strip()) > 180 for opt in options):
            return False

        normalized = [" ".join(str(opt).lower().split()) for opt in options]
        if len(set(normalized)) != 4:
            return False

        weak_opts = {"none of the above", "all of the above", "none of these"}
        if any(opt in weak_opts for opt in normalized):
            return False

        if row.get("correct_answer") not in {"A", "B", "C", "D"}:
            return False

        explanation = str(row.get("explanation") or "").strip()
        if not explanation or len(explanation) > 220:
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
_mcq_generator_lock = threading.Lock()


def get_mcq_generator() -> MCQGenerator:
    global _mcq_generator
    if _mcq_generator is None:
        with _mcq_generator_lock:
            if _mcq_generator is None:
                _mcq_generator = MCQGenerator()
    return _mcq_generator
