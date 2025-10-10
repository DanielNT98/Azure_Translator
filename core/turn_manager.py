"""
Turn management for bidirectional conversation.

This module handles turn-taking logic for two-party conversations,
managing speaking turns with confidence scoring and barge-in detection.
"""

import time
import threading
import logging
from enum import Enum
from config.settings import MIN_SPEECH_MS, SOFT_LOCK_MS, BARGE_THRESHOLD, ENERGY_THRESHOLD


logger = logging.getLogger("translator_bridge")


class TurnState(Enum):
    """
    Enumeration of possible conversation turn states.
    
    Attributes:
        A: Party A has the speaking turn
        B: Party B has the speaking turn  
        IDLE: No one is speaking
    """
    A = "A"
    B = "B"
    IDLE = "IDLE"


class TurnManager:
    """
    Manages conversation turns with confidence-based switching.
    
    Tracks speech activity for both parties (A and B), calculates confidence
    scores, and determines who has the speaking turn. Implements soft-locking
    to prevent rapid turn switching and supports barge-in with high confidence.
    
    Attributes:
        state (TurnState): Current turn state
        write_lock (threading.Lock): Thread-safe state modification lock
        last_switch_ms (float): Timestamp of last turn switch
        speech_start_ms (dict): Speech start times for each channel
        confidence (dict): Current confidence scores [0-1] for each channel
        active_frames (dict): Count of active frames for each channel
        turn_started_at (float): Timestamp when current turn started
        max_energy (dict): Maximum observed energy for each channel
        energy_ema (dict): Exponential moving average of energy
        ema_alpha (float): EMA smoothing factor
    """
    
    def __init__(self):
        """Initialize turn manager with default state."""
        self.state = TurnState.IDLE
        self.write_lock = threading.Lock()
        self.last_switch_ms = 0.0
        self.speech_start_ms = {"A": 0.0, "B": 0.0}
        self.confidence = {"A": 0.0, "B": 0.0}
        self.active_frames = {"A": 0, "B": 0}
        self.turn_started_at = 0.0
        self.max_energy = {"A": 0.0, "B": 0.0}
        self.energy_ema = {"A": 0.0, "B": 0.0}
        self.ema_alpha = 0.3
    
    def update_activity(self, channel, is_active, energy=0.0):
        """
        Update activity state for a channel and potentially switch turns.
        
        Calculates confidence score based on speech duration, frame count,
        and energy level. Determines if turn should switch based on confidence
        and soft-lock timing rules.
        
        Args:
            channel (str): Channel identifier ("A" or "B")
            is_active (bool): Whether channel is currently active
            energy (float, optional): Current energy level
            
        Returns:
            bool: True if turn switched, False otherwise
        """
        try:
            current_time_ms = time.time() * 1000
            other_channel = "B" if channel == "A" else "A"
            
            # Update energy EMA
            self.energy_ema[channel] = (
                self.ema_alpha * energy + 
                (1 - self.ema_alpha) * self.energy_ema[channel]
            )
            smoothed_energy = self.energy_ema[channel]
            
            # Track maximum energy
            if is_active and smoothed_energy > self.max_energy[channel]:
                self.max_energy[channel] = smoothed_energy
            
            if is_active:
                # Channel is active
                self.active_frames[channel] += 1
                
                # Initialize speech start time
                if self.speech_start_ms[channel] == 0.0:
                    self.speech_start_ms[channel] = current_time_ms
                
                # Calculate confidence based on multiple factors
                duration = current_time_ms - self.speech_start_ms[channel]
                frame_factor = min(1.0, self.active_frames[channel] / 5)
                duration_factor = min(1.0, duration / MIN_SPEECH_MS)
                energy_factor = min(2.0, smoothed_energy / ENERGY_THRESHOLD)
                
                self.confidence[channel] = frame_factor * duration_factor * energy_factor * 0.8
                
                # Determine target turn state
                target_state = TurnState.A if channel == "A" else TurnState.B
                time_in_turn = current_time_ms - self.turn_started_at
                should_switch = False
                
                # Check if we should switch turns
                if self.state == TurnState.IDLE or time_in_turn >= SOFT_LOCK_MS:
                    # No soft lock, check confidence
                    if self.confidence[channel] >= 0.6:
                        should_switch = True
                elif (self.state != target_state and 
                      time_in_turn < SOFT_LOCK_MS and
                      smoothed_energy >= BARGE_THRESHOLD and
                      self.confidence[channel] >= 0.75 and
                      self.active_frames[channel] >= 5):
                    # Barge-in with high confidence
                    should_switch = True
                
                if should_switch and self.state != target_state:
                    self._switch_turn(target_state, current_time_ms)
                    return True
            else:
                # Channel is inactive
                self.active_frames[channel] = max(0, self.active_frames[channel] - 1)
                if self.active_frames[channel] == 0:
                    self._reset_channel(channel)
            
            return False
            
        except Exception as e:
            logger.error(f"Error updating activity: {e}")
            return False
    
    def _switch_turn(self, new_state, timestamp_ms):
        """
        Switch to new turn state.
        
        Args:
            new_state (TurnState): New turn state
            timestamp_ms (float): Timestamp of switch
        """
        with self.write_lock:
            old_state = self.state
            self.state = new_state
            self.last_switch_ms = timestamp_ms
            self.turn_started_at = timestamp_ms
            logger.info(f"TURN SWITCH {old_state.value} -> {new_state.value}")
    
    def _reset_channel(self, channel):
        """
        Reset all metrics for a channel.
        
        Args:
            channel (str): Channel identifier ("A" or "B")
        """
        self.confidence[channel] = 0.0
        self.speech_start_ms[channel] = 0.0
        self.active_frames[channel] = 0
        self.max_energy[channel] = 0.0
        self.energy_ema[channel] = 0.0
    
    def get_state(self):
        """
        Get current turn state.
        
        Returns:
            TurnState: Current turn state
        """
        return self.state
