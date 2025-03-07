import asyncio
import random
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, List, Dict, Optional

import discord
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands
from discord.utils import format_dt

from ballsdex.settings import settings
from ballsdex.core.models import BallInstance, Player
from ballsdex.core.utils.transformers import BallInstanceTransform

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot


class Card:
    """Represents a playing card"""
    suits = ["♠️", "♥️", "♦️", "♣️"]
    ranks = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
    
    def __init__(self, suit: str, rank: str):
        self.suit = suit
        self.rank = rank
    
    def __str__(self) -> str:
        return f"{self.rank}{self.suit}"
    
    def value(self) -> int:
        if self.rank == "A":
            return 11  # Ace is initially 11, can be 1 if needed
        elif self.rank in ["J", "Q", "K"]:
            return 10
        else:
            return int(self.rank)


class BlackjackGame:
    """Represents a blackjack game with dealer and player hands"""
    
    def __init__(self):
        self.deck = self._create_deck()
        self.player_hand: List[Card] = []
        self.dealer_hand: List[Card] = []
        self.player_stand = False
        self.game_over = False
        self.result = None
        self.insurance_offered = False
        self.insurance_taken = False
        self.doubled_down = False
        self.bet_multiplier = 1.0
        self.split_hands = []  # Will contain additional hands if split
        self.current_hand_index = 0  # Which hand is active (0 = original hand)
        self.split_performed = False
        
        # Initial deal
        self.player_hand.append(self.draw_card())
        self.dealer_hand.append(self.draw_card())
        self.player_hand.append(self.draw_card())
        self.dealer_hand.append(self.draw_card())
    
    def _create_deck(self) -> List[Card]:
        """Create and shuffle a new deck of cards"""
        deck = [Card(suit, rank) for suit in Card.suits for rank in Card.ranks]
        random.shuffle(deck)
        return deck
    
    def draw_card(self) -> Card:
        """Draw a card from the deck"""
        if not self.deck:
            self.deck = self._create_deck()
        return self.deck.pop()
    
    def player_split(self):
        """Split the player's hand into two separate hands"""
        if not self.can_split():
            return False
        
        # Create a new hand with the second card
        split_card = self.player_hand.pop()
        new_hand = [split_card]
        
        # Add a card to each hand
        self.player_hand.append(self.draw_card())
        new_hand.append(self.draw_card())
        
        # Add the new hand to the split hands list
        self.split_hands.append(new_hand)
        self.split_performed = True
        
        # Check for blackjack in either hand
        if self.get_hand_value(self.player_hand) == 21:
            # If current hand is 21, automatically stand and move to next hand
            self.switch_to_next_split_hand()
        
        return True
    
    def get_current_hand(self):
        """Get the currently active hand"""
        if self.current_hand_index == 0:
            return self.player_hand
        else:
            return self.split_hands[self.current_hand_index - 1]
    
    def switch_to_next_split_hand(self):
        """Move to the next split hand or finish if all hands have been played"""
        self.current_hand_index += 1
        
        # If we've played all hands, dealer plays
        if self.current_hand_index >= len(self.split_hands) + 1:
            self.player_stand = True
            self.dealer_play()
            return False
        
        # If the new hand is 21, automatically move to next
        current_hand = self.get_current_hand()
        if self.get_hand_value(current_hand) == 21:
            return self.switch_to_next_split_hand()
        
        return True
    
    def player_hit(self):
        """Player draws a card for the current hand"""
        current_hand = self.get_current_hand()
        card = self.draw_card()
        current_hand.append(card)
        
        if self.get_hand_value(current_hand) > 21:
            # If this hand busts, move to next hand
            if self.split_performed:
                self.switch_to_next_split_hand()
            else:
                self.game_over = True
                self.result = "BUST"
        
        return card
    
    def player_double_down(self) -> Card:
        """Player doubles down - doubles bet, takes one card, then stands"""
        self.doubled_down = True
        self.bet_multiplier = 2.0
        card = self.draw_card()
        self.player_hand.append(card)
        self.player_stand = True
        if self.get_hand_value(self.player_hand) > 21:
            self.game_over = True
            self.result = "BUST"
        else:
            self.dealer_play()
        return card
    
    def player_stands(self):
        """Player stands - dealer plays their hand or moves to next split hand"""
        if self.split_performed and self.current_hand_index < len(self.split_hands):
            # If we're playing with split hands, move to the next hand
            self.switch_to_next_split_hand()
        else:
            # If this is the last hand or no split, stand and dealer plays
            self.player_stand = True
            self.dealer_play()
    
    def dealer_play(self):
        """Dealer plays against all hands"""
        # Only play if all hands have been played
        if self.split_performed and self.current_hand_index <= len(self.split_hands):
            return
        
        # Dealer plays as normal
        while self.get_hand_value(self.dealer_hand) < 17:
            self.dealer_hand.append(self.draw_card())
        
        dealer_value = self.get_hand_value(self.dealer_hand)
        self.game_over = True
        
        # If split was performed, evaluate all hands
        if self.split_performed:
            self.results = []  # Results for each hand
            all_hands = [self.player_hand] + self.split_hands
            
            for hand in all_hands:
                player_value = self.get_hand_value(hand)
                
                if player_value > 21:
                    self.results.append("BUST")
                elif dealer_value > 21:
                    self.results.append("WIN")
                elif dealer_value > player_value:
                    self.results.append("LOSE")
                elif dealer_value < player_value:
                    self.results.append("WIN")
                else:
                    self.results.append("PUSH")
            
            # Set main result based on first hand
            self.result = self.results[0]
        else:
            # Original logic for single hand
            player_value = self.get_hand_value(self.player_hand)
            
            if dealer_value > 21:
                self.result = "WIN"
            elif dealer_value > player_value:
                self.result = "LOSE"
            elif dealer_value < player_value:
                self.result = "WIN"
            else:
                self.result = "PUSH"
    
    def get_hand_value(self, hand: List[Card]) -> int:
        """Calculate the value of a hand, accounting for Aces"""
        value = sum(card.value() for card in hand)
        # Adjust for Aces if needed
        aces = sum(1 for card in hand if card.rank == "A")
        while value > 21 and aces > 0:
            value -= 10  # Convert an Ace from 11 to 1
            aces -= 1
        return value
    
    def can_split(self) -> bool:
        """Check if player can split their hand"""
        if len(self.player_hand) != 2:
            return False
        return self.player_hand[0].rank == self.player_hand[1].rank
    
    def can_double_down(self) -> bool:
        """Check if player can double down (only with first two cards)"""
        return len(self.player_hand) == 2 and not self.player_stand
    
    def can_take_insurance(self) -> bool:
        """Check if insurance is available (dealer's up card is an Ace)"""
        return len(self.dealer_hand) == 2 and self.dealer_hand[0].rank == "A" and not self.insurance_offered
    
    def offer_insurance(self):
        """Mark insurance as offered"""
        self.insurance_offered = True
    
    def take_insurance(self):
        """Player takes insurance bet"""
        self.insurance_taken = True
        # Insurance pays 2:1 if dealer has blackjack
        has_dealer_blackjack = (self.dealer_hand[0].rank == "A" and self.dealer_hand[1].value() == 10) or \
                              (self.dealer_hand[1].rank == "A" and self.dealer_hand[0].value() == 10)
        return has_dealer_blackjack
    
    def check_natural_blackjack(self) -> bool:
        """Check if player has a natural blackjack (21 with first two cards)"""
        return len(self.player_hand) == 2 and self.get_hand_value(self.player_hand) == 21


class BlackjackGameView(discord.ui.View):
    """Interactive view for a blackjack game"""
    
    def __init__(self, bot: "BallsDexBot", player: Player, game: BlackjackGame, countryball: BallInstance):
        super().__init__(timeout=180)  # 3 minute timeout
        self.bot = bot
        self.player = player
        self.game = game
        self.countryball = countryball
        self.message: Optional[discord.Message] = None
        self.interaction: Optional[discord.Interaction] = None
        
        # Check for natural blackjack
        if self.game.check_natural_blackjack():
            self.game.game_over = True
            self.game.result = "BLACKJACK"
        
        # Check if insurance should be offered
        if self.game.can_take_insurance():
            self.game.offer_insurance()
        
        self._update_buttons()
    
    async def on_timeout(self):
        """Handle timeout - player automatically stands"""
        if not self.game.player_stand and not self.game.game_over:
            self.game.player_stand()
            self.game.dealer_play()
            await self._update_message()
    
    def _update_buttons(self):
        """Update button states based on current game state"""
        # Disable all buttons if game is over
        if self.game.game_over:
            for item in self.children:
                item.disabled = True
            return
        
        # Set button states based on game state
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                if item.custom_id == "hit":
                    item.disabled = self.game.player_stand
                elif item.custom_id == "stand":
                    item.disabled = self.game.player_stand
                elif item.custom_id == "double":
                    item.disabled = not self.game.can_double_down()
                elif item.custom_id == "split":
                    item.disabled = not self.game.can_split()
                elif item.custom_id == "insurance":
                    item.disabled = not self.game.can_take_insurance() or self.game.insurance_taken
    
    async def send_initial_message(self, interaction: discord.Interaction):
        """Send the initial game message"""
        self.interaction = interaction
        embed = self._create_game_embed()
        await interaction.response.send_message(embed=embed, view=self)
        self.message = await interaction.original_response()
    
    async def _update_message(self):
        """Update the game message with current state"""
        if not self.message:
            return
        
        self._update_buttons()
        embed = self._create_game_embed()
        await self.message.edit(embed=embed, view=self)
    
    def _create_game_embed(self) -> discord.Embed:
        """Create an embed showing the current game state"""
        # Show all dealer cards only if game is over or player stands
        if self.game.game_over or self.game.player_stand:
            dealer_hand_str = " ".join(str(card) for card in self.game.dealer_hand)
            dealer_value = self.game.get_hand_value(self.game.dealer_hand)
        else:
            # Hide dealer's hole card
            dealer_hand_str = f"{self.game.dealer_hand[0]} 🂠"
            dealer_value = "?"
        
        embed = discord.Embed(
            title="Blackjack",
            color=discord.Color.dark_gold(),
        )
        
        # Set bet info
        bet_multiplier = "2x" if self.game.doubled_down else "1x"
        embed.add_field(
            name="Your Bet",
            value=f"{self.countryball.description(include_emoji=True, bot=self.bot, is_trade=False)}\nMultiplier: {bet_multiplier}",
            inline=False,
        )
        
        # Set dealer hand
        embed.add_field(
            name=f"Dealer's Hand ({dealer_value})",
            value=dealer_hand_str,
            inline=False,
        )
        
        # Handle display of player's hands
        if self.game.split_performed:
            # If split was performed, show all split hands
            all_hands = [self.game.player_hand] + self.game.split_hands
            
            for i, hand in enumerate(all_hands):
                hand_str = " ".join(str(card) for card in hand)
                hand_value = self.game.get_hand_value(hand)
                
                # Highlight current hand
                if i == self.game.current_hand_index:
                    embed.add_field(
                        name=f"➡️ Your Hand {i+1} ({hand_value})",
                        value=hand_str,
                        inline=False,
                    )
                else:
                    embed.add_field(
                        name=f"Your Hand {i+1} ({hand_value})",
                        value=hand_str,
                        inline=False,
                    )
            
            # Show results for each hand if game is over
            if self.game.game_over and hasattr(self.game, 'results'):
                result_str = ""
                for i, result in enumerate(self.game.results):
                    result_text = {
                        "WIN": "Win",
                        "LOSE": "Loss",
                        "PUSH": "Push",
                        "BUST": "Bust",
                        "BLACKJACK": "Blackjack!"
                    }.get(result, result)
                    result_str += f"Hand {i+1}: {result_text}\n"
                
                embed.add_field(
                    name="Results",
                    value=result_str,
                    inline=False,
                )
        else:
            # Original single hand display (only if no split)
            player_hand_str = " ".join(str(card) for card in self.game.player_hand)
            player_value = self.game.get_hand_value(self.game.player_hand)
            
            embed.add_field(
                name=f"Your Hand ({player_value})",
                value=player_hand_str,
                inline=False,
            )
        
        # Set result if game is over
        if self.game.game_over:
            if self.game.result == "BLACKJACK":
                embed.description = "**BLACKJACK!** You win with a natural 21! (Payout: 3:2)"
                embed.color = discord.Color.green()
            elif self.game.result == "WIN":
                embed.description = "**You win!**"
                embed.color = discord.Color.green()
            elif self.game.result == "LOSE":
                embed.description = "**You lose.**"
                embed.color = discord.Color.red()
            elif self.game.result == "BUST":
                embed.description = "**BUST!** You went over 21."
                embed.color = discord.Color.red()
            elif self.game.result == "PUSH":
                embed.description = "**PUSH.** It's a tie!"
                embed.color = discord.Color.blue()
                
            # Add insurance result if applicable
            if self.game.insurance_taken:
                dealer_blackjack = (
                    len(self.game.dealer_hand) == 2 and 
                    self.game.get_hand_value(self.game.dealer_hand) == 21
                )
                if dealer_blackjack:
                    embed.add_field(
                        name="Insurance",
                        value="Your insurance paid off! Dealer had blackjack.",
                        inline=False,
                    )
                else:
                    embed.add_field(
                        name="Insurance",
                        value="Your insurance didn't pay off. Dealer didn't have blackjack.",
                        inline=False,
                    )
        
        return embed
    
    async def _process_action(self, interaction: discord.Interaction, action_fn, *args):
        """Process a player action with proper response handling"""
        # Acknowledge the interaction first to prevent timeout
        await interaction.response.defer()
        
        # Perform the action
        action_fn(*args)
        
        # Update the message with the new game state
        await self._update_message()
        
        # Handle game end
        if self.game.game_over:
            # Process results
            await self._process_game_results()
    
    async def _process_game_results(self):
        """Process the game results - handle rewards or penalties"""
        if not self.game.game_over:
            return
            
        ball = await self.countryball.ball.first()
        
        # If split was performed, process results for each hand
        if self.game.split_performed and hasattr(self.game, 'results'):
            # Count wins to determine payout
            win_count = sum(1 for result in self.game.results if result in ["WIN", "BLACKJACK"])
            push_count = sum(1 for result in self.game.results if result == "PUSH")
            
            # Always delete the original bet ball when splitting
            await self.countryball.delete()
            
            # Create new balls for wins (1:1 payout)
            for _ in range(win_count):
                await BallInstance.create(
                    player=self.player,
                    ball=ball,
                    attack_bonus=random.randint(-20, 20),
                    health_bonus=random.randint(-20, 20),
                )
            
            # Create new balls for pushed hands (return original bet)
            for _ in range(push_count):
                await BallInstance.create(
                    player=self.player,
                    ball=ball,
                    attack_bonus=random.randint(-20, 20),
                    health_bonus=random.randint(-20, 20),
                )
            
            # Double the payouts if double down was performed
            if self.game.doubled_down:
                additional_wins = win_count
                for _ in range(additional_wins):
                    await BallInstance.create(
                        player=self.player,
                        ball=ball,
                        attack_bonus=random.randint(-20, 20),
                        health_bonus=random.randint(-20, 20),
                    )
        else:
            # First determine if player keeps their original ball
            keep_original = self.game.result in ["BLACKJACK", "WIN", "PUSH"]
            
            # Create additional balls based on result
            if self.game.result == "BLACKJACK":
                # Blackjack pays 3:2 (player keeps original and gets 1.5x more)
                # Since we can only give whole balls, round up to 2
                reward_count = 2
                for _ in range(reward_count):
                    await BallInstance.create(
                        player=self.player,
                        ball=ball,
                        attack_bonus=random.randint(-20, 20),
                        health_bonus=random.randint(-20, 20),
                    )
            elif self.game.result == "WIN":
                # Regular win pays 1:1 (keep original plus equal amount)
                # Apply bet multiplier for double down
                reward_count = int(self.game.bet_multiplier)
                for _ in range(reward_count):
                    await BallInstance.create(
                        player=self.player,
                        ball=ball,
                        attack_bonus=random.randint(-20, 20),
                        health_bonus=random.randint(-20, 20),
                    )
            elif self.game.result == "PUSH":
                # Push - keep original, no additional balls
                pass
            
            # Delete the original ball only if it's a loss
            if not keep_original:
                await self.countryball.delete()
        
        # Handle insurance if applicable
        if self.game.insurance_taken:
            dealer_blackjack = (
                len(self.game.dealer_hand) == 2 and 
                self.game.get_hand_value(self.game.dealer_hand) == 21
            )
            if dealer_blackjack:
                # Insurance pays 2:1
                for _ in range(2):
                    await BallInstance.create(
                        player=self.player,
                        ball=ball,
                        attack_bonus=random.randint(-20, 20),
                        health_bonus=random.randint(-20, 20),
                    )
    
    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary, custom_id="hit")
    async def hit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Player takes another card"""
        await self._process_action(interaction, self.game.player_hit)
    
    @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary, custom_id="stand")
    async def stand_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Player stands (no more cards)"""
        await interaction.response.defer()
        
        if self.game.split_performed and self.game.current_hand_index < len(self.game.split_hands):
            # In split mode, move to next hand
            self.game.switch_to_next_split_hand()
        else:
            # Last hand or no split
            self.game.player_stands()
        
        await self._update_message()
        
        if self.game.game_over:
            await self._process_game_results()
    
    @discord.ui.button(label="Double Down", style=discord.ButtonStyle.success, custom_id="double")
    async def double_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Player doubles down - doubles bet, takes one card, then stands"""
        await self._process_action(interaction, self.game.player_double_down)
    
    @discord.ui.button(label="Insurance", style=discord.ButtonStyle.danger, custom_id="insurance")
    async def insurance_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Player takes insurance against dealer blackjack"""
        await self._process_action(interaction, self.game.take_insurance)
        
    @discord.ui.button(label="Split", style=discord.ButtonStyle.danger, custom_id="split")
    async def split_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Split the player's hand into two separate hands"""
        await self._process_action(interaction, self.game.player_split)
        await interaction.followup.send("Hand split into two hands!", ephemeral=True)