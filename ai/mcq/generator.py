"""Topic-based MCQ generator using local LLM."""

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

from web_context import build_topic_web_context


class MCQGenerator:
    """
    Generates Multiple Choice Questions using a language model.
    This class is responsible for prompt engineering, model inference, and parsing the output.
    """
    def __init__(self) -> None:
        self.llm = None
        self._llm_lock = threading.Lock()
        self._result_cache: OrderedDict[tuple, List[Dict]] = OrderedDict()
        self._cache_limit = 128
        self.last_generation_meta: Dict = {}

    def ensure_model_loaded(self):
        """Initializes the language model if it hasn't been already."""
        from models.llm import get_llm_model
        if self.llm is not None:
            return self.llm
        with self._llm_lock:
            if self.llm is None:
                self.llm = get_llm_model()
        return self.llm

    def is_model_loaded(self) -> bool:
        """Checks if the model has been loaded into memory."""
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
        """Creates a unique tuple key for caching generation requests."""
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
        """
        Main entry point for question generation.
        Handles caching, context fetching, and orchestration of the generation process.
        """
        self.ensure_model_loaded()

        safe_topic = str(topic or "").strip()
        safe_test_title = str(test_title or "").strip()
        safe_test_description = str(test_description or "").strip()
        # If no specific topic is given, derive one from the test title or description
        if not safe_topic:
            safe_topic = " ".join(part for part in [safe_test_title, safe_test_description] if part).strip() or "general science"

        requested_count = int(max(1, min(num_questions, 50)))
        cache_key = self._cache_key(
            topic=safe_topic, num_questions=requested_count, difficulty=difficulty,
            subject=subject, grade=grade, seed=seed, test_title=safe_test_title, test_description=safe_test_description,
        )

        cached_rows = self._result_cache.get(cache_key)
        if cached_rows is not None:
            self._result_cache.move_to_end(cache_key)
            return deepcopy(cached_rows[:requested_count])

        # Fetch external web context to ground the model in facts
        context = build_topic_web_context(safe_topic)
        facts = self._extract_facts(context, safe_topic)

        # Generate questions using the language model
        final_rows = self._generate_llm_mcqs(
            topic=safe_topic,
            subject=subject,
            grade=grade,
            difficulty=difficulty,
            requested_count=requested_count,
            facts=facts,
            test_title=safe_test_title,
            test_description=safe_test_description,
            seed=seed,
        )

        self.last_generation_meta = {
            "requested": requested_count,
            "produced": len(final_rows),
            "llm_count": len(final_rows),
            "template_count": 0,
            "facts_count": len(facts),
            "cache_hit": False,
        }

        # Cache the result
        if final_rows:
            self._result_cache[cache_key] = deepcopy(final_rows)
            if len(self._result_cache) > self._cache_limit:
                self._result_cache.popitem(last=False)

        return final_rows

    def _grade_guidance(self, grade: str) -> str:
        """Provides specific instructions based on the grade level."""
        grade_key = str(grade or "").strip().lower()
        if grade_key == "elementary":
            return "Use simple, concrete vocabulary suitable for young learners. Focus on foundational concepts."
        if grade_key == "middle":
            return "Use standard academic vocabulary. Questions can involve 1-2 steps of reasoning."
        if grade_key == "high":
            return "Use rigorous, exam-style language. Questions should test for deep conceptual understanding and application."
        if grade_key == "college":
            return "Use concise, technical language. Assume foundational knowledge and test deeper or more abstract concepts."
        return "Match question wording to a standard curriculum for the grade level."

    def _difficulty_guidance(self, difficulty: str) -> str:
        """Provides specific instructions based on the difficulty level."""
        difficulty_key = str(difficulty or "").strip().lower()
        if difficulty_key == "easy":
            return "Focus on direct fact recall and single-step problems. Distractors can be clearly incorrect."
        if difficulty_key == "medium":
            return "Require application of concepts or 2-3 steps of reasoning. Distractors should be plausible."
        if difficulty_key == "hard":
            return "Require synthesis of multiple concepts or nuanced, multi-step reasoning. Distractors should target common misconceptions."
        return "Align the question's challenge level to the requested difficulty."

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
    ) -> str:
        """
        Engineers a detailed, context-rich prompt for the language model.
        This is the core of the AI's "intelligence".
        """
        grade_guidance = self._grade_guidance(grade)
        difficulty_guidance = self._difficulty_guidance(difficulty)

        # Create a context block for the test, if provided
        test_context_parts = []
        if test_title:
            test_context_parts.append(f"The test is titled '{test_title}'.")
        if test_description:
            test_context_parts.append(f"Its description is: '{test_description}'.")
        test_context = " ".join(test_context_parts)

        # Provide a limited number of facts to keep the prompt focused
        fact_lines = "\n".join(f"- {fact}" for fact in facts[:15])

        # The main instruction block, framed as a persona
        return (
            f"You are an expert {subject} curriculum developer creating an assessment for {grade} students.\n"
            f"Your task is to generate {num_questions} high-quality multiple-choice questions about '{topic}'.\n"
            f"{test_context}\n"
            f"Adhere to these rules:\n"
            f"1. Grade Level: {grade_guidance}\n"
            f"2. Difficulty: {difficulty_guidance}\n"
            f"3. Grounding: Base questions on the provided facts. Do not invent information.\n"
            f"4. Format: For each question, you must strictly follow this format, with no extra text:\n"
            "Question: <The question text ending with a question mark>\n"
            "A) <Option A text>\n"
            "B) <Option B text>\n"
            "C) <Option C text>\n"
            "D) <Option D text>\n"
            "Answer: <The correct option letter, e.g., A>\n"
            "Explanation: <A single, concise sentence explaining why the answer is correct>\n\n"
            f"Begin now. Here are the facts to use:\n"
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
        seed: Optional[int] = None,
    ) -> List[Dict]:
        """
        Calls the language model and parses its output.
        Includes a timeout to ensure service reliability.
        """
        if self.llm is None:
            return []
        
        # Set a seed for reproducibility if provided
        if seed is not None:
            torch.manual_seed(seed)
            random.seed(seed)

        prompt = self._create_llm_prompt(
            topic=topic, subject=subject, grade=grade, num_questions=requested_count,
            difficulty=difficulty, facts=facts, test_title=test_title, test_description=test_description
        )
        
        # The generation has a hard timeout to prevent the service from crashing.
        # 30 seconds is a safe but generous limit for this task.
        response = self.llm.generate(
            prompt=prompt,
            max_new_tokens=2048,  # Generous token limit
            temperature=0.2,     # Low temperature for more focused, factual output
            top_p=0.95,
            max_time=30.0,
        )
        
        return self._parse_llm_output(response, difficulty, topic)

    def _parse_llm_output(self, response: str, difficulty: str, topic: str) -> List[Dict]:
        """
        Parses the plain text output from the LLM into a structured list of question dictionaries.
        This function is designed to be robust to minor formatting errors from the model.
        """
        candidates: List[Dict] = []
        text = str(response or "").strip()

        # Split the entire response into blocks, where each block starts with "Question:"
        question_blocks = re.split(r"(?=^Question:)", text, flags=re.MULTILINE)

        for block in question_blocks:
            block = block.strip()
            if not block:
                continue

            try:
                question_match = re.search(r"^Question:\s*(.+)", block, re.MULTILINE)
                options_matches = re.findall(r"^[A-D]\)\s*(.+)", block, re.MULTILINE)
                answer_match = re.search(r"^Answer:\s*([A-D])", block, re.MULTILINE)
                explanation_match = re.search(r"^Explanation:\s*(.+)", block, re.MULTILINE)

                if question_match and len(options_matches) == 4 and answer_match and explanation_match:
                    question = question_match.group(1).strip()
                    options = [opt.strip() for opt in options_matches]
                    answer = answer_match.group(1).strip()
                    explanation = explanation_match.group(1).strip()
                    
                    normalized = self._normalize_candidate(
                        item={
                            "question": question,
                            "options": options,
                            "answer": answer,
                            "explanation": explanation,
                        },
                        difficulty=difficulty,
                        topic=topic,
                        source="llm_v2"
                    )
                    if normalized:
                        candidates.append(normalized)

            except Exception:
                # Ignore blocks that fail to parse
                continue
                
        return self._dedupe_questions(candidates)

    def _normalize_candidate(self, item: Dict, difficulty: str, topic: str, source: str) -> Optional[Dict]:
        """Validates and standardizes a single parsed question dictionary."""
        question = str(item.get("question") or "").strip()
        if not question.endswith("?"):
            question += "?"

        options = item.get("options")
        if not isinstance(options, list) or len(options) != 4:
            return None
        
        answer = str(item.get("answer") or "").strip().upper()
        if answer not in ["A", "B", "C", "D"]:
            return None
            
        explanation = str(item.get("explanation") or "").strip()
        if not all([question, explanation]):
            return None

        correct_index = ["A", "B", "C", "D"].index(answer)

        return {
            "question": question,
            "options": options,
            "correct_answer": answer,
            "correct_index": correct_index,
            "explanation": explanation,
            "hint": f"Review the facts about {topic} related to this question.",
            "difficulty": difficulty,
            "topic": topic,
            "source": source,
        }

    def _dedupe_questions(self, rows: List[Dict]) -> List[Dict]:
        """Removes duplicate questions based on text and options."""
        unique: List[Dict] = []
        seen = set()
        for row in rows:
            question_key = " ".join(str(row.get("question", "")).lower().split())
            options_key = "|".join(sorted(" ".join(str(opt).lower().split()) for opt in row.get("options", [])))
            key = f"{question_key}|{options_key}"
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(row)
        return unique
    
    def _extract_facts(self, context: str, topic: str) -> List[str]:
        """A simple utility to extract sentences from web context."""
        text = str(context or "").replace("\r", " ").strip()
        # Basic sentence splitting
        sentences = re.split(r'(?<=[.!?])\s+', text)
        
        facts = [s.strip() for s in sentences if len(s.strip()) > 20 and len(s.strip()) < 300]
        
        if not facts:
            return [f"{topic} is a key area of study in this subject."]
        return facts


_mcq_generator: MCQGenerator | None = None
_mcq_generator_lock = threading.Lock()


def get_mcq_generator() -> MCQGenerator:
    """Singleton factory for the MCQGenerator."""
    global _mcq_generator
    if _mcq_generator is None:
        with _mcq_generator_lock:
            if _mcq_generator is None:
                _mcq_generator = MCQGenerator()
    return _mcq_generator
