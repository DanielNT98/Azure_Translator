"""
Intelligent text chunking for streaming translation.

This module provides balanced text chunking that splits partial translations
into speakable segments while avoiding word truncation and respecting natural
language boundaries.
"""

import re
import time
import logging
from typing import Optional
from config.settings import (
    FIRST_CHUNK_WORDS, SUBSEQUENT_CHUNK_WORDS,
    FIRST_CHUNK_STABILITY_MS, SUBSEQUENT_STABILITY_MS,
    MAX_CHUNK_DELAY_MS, MAX_PARTIAL_DURATION_MS,
    GROWTH_THRESHOLD_WORDS, MIN_DELAY_BETWEEN_CHUNKS_MS
)


logger = logging.getLogger("translator_bridge")


class BalancedChunker:
    """
    Intelligent text chunker with word validation and boundary detection.
    
    Splits streaming partial text into chunks for immediate speech synthesis
    while avoiding word truncation and respecting sentence boundaries. Uses
    stability timeouts and word count thresholds to balance latency vs quality.
    
    Attributes:
        label (str): Label for logging identification
        last_partial (str): Last seen partial text
        last_spoken_indices (list): Indices of words already spoken
        last_change_time (float): Timestamp of last text change
        previous_partial (str): Previous partial for change detection
        first_chunk_spoken (bool): Whether first chunk has been emitted
        strong_boundaries (re.Pattern): Regex for strong sentence boundaries
        weak_boundaries (re.Pattern): Regex for weak boundaries (commas)
        stats (dict): Statistics on chunks, finals, and skipped segments
    """
    
    def __init__(self, label="Chunker"):
        """
        Initialize chunker with default settings.
        
        Args:
            label (str, optional): Label for logging. Defaults to "Chunker".
        """
        self.label = label
        self.last_partial = ""
        self.last_spoken_indices = []
        self.last_change_time = 0
        self.previous_partial = ""
        self.first_chunk_spoken = False
        self.strong_boundaries = re.compile(r'[.!?;:]')
        self.weak_boundaries = re.compile(r'[,]')
        self.stats = {"chunks": 0, "finals": 0, "skipped": 0}
        
        # Timing parameters
        self.last_chunk_emission_time = 0
        self.partial_start_time = 0
        self.max_chunk_delay_ms = MAX_CHUNK_DELAY_MS
        self.max_partial_duration_ms = MAX_PARTIAL_DURATION_MS
        self.growth_threshold_words = GROWTH_THRESHOLD_WORDS
        self.min_delay_between_chunks = MIN_DELAY_BETWEEN_CHUNKS_MS
    
    def _find_boundary_after_min(self, words, min_words):
        """
        Find natural language boundary after minimum word count.
        
        Searches for strong boundaries (. ! ? ; :) first, then weak
        boundaries (,) in a small window after the minimum word threshold.
        
        Args:
            words (list): List of words to search
            min_words (int): Minimum words before searching for boundary
            
        Returns:
            int or None: Index of word after boundary, or None if not found
        """
        if len(words) <= min_words:
            return None
        
        # Search in small window after minimum
        search_window = words[min_words:min(len(words), min_words + 4)]
        search_text = ' '.join(search_window)
        
        # Check for strong boundaries first
        strong_match = self.strong_boundaries.search(search_text)
        if strong_match:
            text_until = search_text[:strong_match.end()]
            return min_words + len(text_until.split())
        
        # Check for weak boundaries
        weak_match = self.weak_boundaries.search(search_text)
        if weak_match:
            text_until = search_text[:weak_match.end()]
            return min_words + len(text_until.split())
        
        return None
    
    def _is_word_complete(self, word):
        """
        Check if word appears complete (not truncated).
        
        Uses heuristics: very short words without punctuation are suspect,
        words ending in punctuation are complete, words 3+ chars likely complete.
        
        Args:
            word (str): Word to check
            
        Returns:
            bool: True if word appears complete
        """
        if not word:
            return False
        
        # Very short words without punctuation are suspicious
        if len(word) <= 2:
            return False
        
        # Words ending with punctuation are complete
        if word[-1] in '.!?,;:':
            return True
        
        # Words 3+ characters probably complete
        return len(word) >= 3
    
    def process_partial(self, new_text):
        """
        Process partial translation text and extract chunk if ready.
        
        Analyzes new partial text and determines if a chunk should be emitted
        based on stability timeout, word count, and word completion validation.
        
        Args:
            new_text (str): New partial translation text
            
        Returns:
            str or None: Text chunk to speak, or None if not ready
        """
        if not new_text or not new_text.strip():
            return None
        
        new_text = new_text.strip()
        now_ms = time.time() * 1000
        
        # Initialization on first text
        if not self.last_partial:
            self.last_partial = new_text
            self.previous_partial = new_text
            self.last_change_time = now_ms
            self.partial_start_time = now_ms
            if self.last_chunk_emission_time == 0:
                self.last_chunk_emission_time = now_ms
            return None
        
        # Update change timestamp if text changed
        if new_text != self.previous_partial:
            self.last_change_time = now_ms
            self.previous_partial = new_text
        
        # Calculate timing metrics
        stability_time = now_ms - self.last_change_time
        time_since_last_chunk = now_ms - self.last_chunk_emission_time
        partial_duration = now_ms - self.partial_start_time
        
        # Hysteresis: Don't emit too frequently
        if time_since_last_chunk < self.min_delay_between_chunks:
            self.last_partial = new_text
            return None
        
        # Determine dynamic parameters with dual minimum thresholds
        if not self.first_chunk_spoken:
            min_words_normal = FIRST_CHUNK_WORDS  # 2
            min_words_safety = FIRST_CHUNK_WORDS + 2  # 4
            required_stability = FIRST_CHUNK_STABILITY_MS
        else:
            min_words_normal = SUBSEQUENT_CHUNK_WORDS  # 5
            min_words_safety = SUBSEQUENT_CHUNK_WORDS + 2  # 7
            required_stability = SUBSEQUENT_STABILITY_MS
        
        # Analyze words
        words = new_text.split()
        num_spoken = len(self.last_spoken_indices)
        unpronounced_words = words[num_spoken:]
        unpronounced_count = len(unpronounced_words)
        
        # Pre-emission validation: last word must appear complete
        if unpronounced_words and not self._is_word_complete(unpronounced_words[-1]):
            # Require more stability if last word seems truncated
            if stability_time < required_stability * 1.5:
                self.last_partial = new_text
                return None
        
        # Two-level system with differentiated word minimums
        should_emit = False
        emission_reason = ""
        
        # Level 1: Normal operation (highest priority)
        if stability_time >= required_stability and unpronounced_count >= min_words_normal:
            # Absolute preference for linguistic boundaries
            boundary_pos = self._find_boundary_after_min(unpronounced_words, min_words_normal)
            if boundary_pos or stability_time >= required_stability * 1.5:
                should_emit = True
                emission_reason = f"STABLE:{stability_time:.0f}ms"
        
        # Level 2: Safety net (higher word minimum)
        elif time_since_last_chunk >= self.max_chunk_delay_ms and unpronounced_count >= min_words_safety:
            should_emit = True
            emission_reason = f"TIMEOUT:{time_since_last_chunk:.0f}ms"
        
        elif unpronounced_count >= self.growth_threshold_words:
            should_emit = True
            emission_reason = f"GROWTH:{unpronounced_count}w"
        
        elif partial_duration >= self.max_partial_duration_ms and unpronounced_count >= min_words_safety:
            should_emit = True
            emission_reason = f"FORCED:{partial_duration:.0f}ms"
        
        if not should_emit:
            self.last_partial = new_text
            return None
        
        # Select chunk with priority for natural boundaries
        boundary_pos = self._find_boundary_after_min(unpronounced_words, min_words_normal)
        if boundary_pos:
            chunk_words = unpronounced_words[:boundary_pos]
        else:
            # No boundary: use safety minimum to avoid fragments
            target_words = max(min_words_safety, min_words_normal + 3)
            chunk_words = unpronounced_words[:min(unpronounced_count, target_words)]
        
        # Final validation: don't emit chunk with suspicious final word
        if chunk_words and not self._is_word_complete(chunk_words[-1]):
            self.last_partial = new_text
            return None
        
        chunk = ' '.join(chunk_words)
        
        # Update spoken indices
        for i in range(num_spoken, num_spoken + len(chunk_words)):
            self.last_spoken_indices.append(i)
        
        # Update statistics and timestamp
        self.stats["chunks"] += 1
        self.last_chunk_emission_time = now_ms
        self.first_chunk_spoken = True
        self.last_change_time = now_ms
        
        mode = "FIRST" if self.stats["chunks"] == 1 else "FLOW"
        logger.info(f"{self.label} CHUNK#{self.stats['chunks']} [{mode}|{emission_reason}|{len(chunk_words)}w]: {chunk}")
        
        return chunk
    
    def finalize(self, final_text):
        """
        Process final translation text and extract remaining words.
        
        Compares final text with already spoken words and returns
        only the new/remaining portion, or None if all already spoken.
        
        Args:
            final_text (str): Final complete translation text
            
        Returns:
            str or None: Remaining text to speak, or None if complete
        """
        if not final_text or not final_text.strip():
            return None
        
        final_text = final_text.strip()
        final_words = final_text.split()
        
        # If nothing spoken yet, return full text
        if not self.last_spoken_indices:
            self.stats["finals"] += 1
            return final_text
        
        # If final has fewer words than spoken, skip
        if len(final_words) <= len(self.last_spoken_indices):
            logger.info(f"{self.label} SKIP final (complete)")
            self.stats["skipped"] += 1
            return None
        
        # Extract remaining words
        remaining_words = final_words[len(self.last_spoken_indices):]
        
        if not remaining_words:
            logger.info(f"{self.label} COMPLETE")
            self.stats["skipped"] += 1
            return None
        
        remaining = ' '.join(remaining_words)
        self.stats["finals"] += 1
        logger.info(f"{self.label} FINAL: +{len(remaining_words)}w")
        return remaining
    
    def reset(self):
        """Reset chunker state for new phrase."""
        if self.stats["chunks"] > 0 or self.stats["finals"] > 0:
            logger.info(
                f"{self.label} STATS - Chunks:{self.stats['chunks']} "
                f"Finals:{self.stats['finals']} Skipped:{self.stats['skipped']}"
            )
        self.last_partial = ""
        self.last_spoken_indices = []
        self.previous_partial = ""
        self.last_change_time = 0
        self.first_chunk_spoken = False
        self.partial_start_time = 0
        self.stats = {k: 0 for k in self.stats}
