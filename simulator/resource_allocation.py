import random
from helpers import *

class ResourceAllocation:
    def __init__(self, bandwidth_mhz, numerology, rri, L, H, scheduler, protocol, node):
        self.bandwidth_mhz = bandwidth_mhz
        self.numerology = numerology
        self.frame_duration = int((rri*1000) / 10)
        self.subframes_per_frame = 10
        self.slots_per_subframe = 2 ** numerology
        self.channels_per_slot = self.calculate_channels_per_slot(bandwidth_mhz, numerology)
        self.total_slots = self.frame_duration * self.subframes_per_frame * self.slots_per_subframe
        self.scheduler = scheduler
        self.resource_grid = self.create_resource_grid()
        self.resource_last_used = {}
        self.node = node  # Add this line to store the Node reference
        self.rri=rri
        self.L = L
        self.H = H
        
        

        self.protocol = protocol
        #CRA6G
        self.RS_grid, self.Distance_grid = self.create_RS_grid()
        self.RS_dict, self.Distance_dict = self.create_RS_dict(self.RS_grid, self.Distance_grid)
        self.RS_lasttime=self.node.scheduler.current_time
        self.RS_update_times = {}
        self.RS_last_update={}
        self.updated_in_step1 = {}
        self.rri_RS_factor=2
        self.ava_hold=2.5

    def use_node_function(self, node_id):
        """
        Example of calling a Node function dynamically.
        """
        from node import Node  # Import Node here to avoid circular import
        print(f"Using Node ID: {node_id}")

    def calculate_channels_per_slot(self, bandwidth_mhz, numerology):
        base_channels = {0: 5, 1: 2, 2: 1}
        return base_channels[numerology] * (bandwidth_mhz // 10)

    def create_resource_grid(self):
        return [[False for _ in range(self.channels_per_slot)] for _ in range(self.total_slots)]

    def convert_to_slot_channel(self, frame_idx, subframe_idx, slot_idx, channel_idx):
        slot = frame_idx * self.subframes_per_frame * self.slots_per_subframe + subframe_idx * self.slots_per_subframe + slot_idx
        return (slot, channel_idx)

    def convert_back_to_detailed(self, slot, channel):
        frame_idx = slot // (self.subframes_per_frame * self.slots_per_subframe)
        subframe_idx = (slot % (self.subframes_per_frame * self.slots_per_subframe)) // self.slots_per_subframe
        slot_idx = (slot % (self.subframes_per_frame * self.slots_per_subframe)) % self.slots_per_subframe
        return (frame_idx, subframe_idx, slot_idx, channel)
        

        

    # def select_random_contiguous_channels(self, num_channels):
   
    #     free_resources = self.get_free_resources()
           
    #     suitable_slots = []
    #     slot_contiguous_blocks = {}

        
    #     # Collect slots that have at least one suitable contiguous block
    #     for slot, channels in free_resources.items():
    #         contiguous_blocks = self.find_contiguous_channels(channels, num_channels)
    #         if contiguous_blocks:
    #             suitable_slots.append(slot)
    #             slot_contiguous_blocks[slot] = contiguous_blocks
       
    
    #     if suitable_slots:
    #         # Randomly select a slot first
    #         selected_slot = random.choice(suitable_slots)
    #         # Then randomly select one of its contiguous blocks
    #         selected_block = random.choice(slot_contiguous_blocks[selected_slot])
    #         return [(selected_slot, ch) for ch in selected_block]
    #     return []

    def select_random_contiguous_channels(self, num_channels):
        """
        Select a random contiguous block of channels across all slots.
        """
        free_resources = self.get_free_resources()
        all_contiguous_blocks = []
    
        # Collect all contiguous blocks from all slots
        for slot, channels in free_resources.items():
            contiguous_blocks = self.find_contiguous_channels(channels, num_channels)
            for block in contiguous_blocks:
                all_contiguous_blocks.append([(slot, ch) for ch in block])
    
        # Randomly choose one block if any exist
        if all_contiguous_blocks:
            return random.choice(all_contiguous_blocks)
        
        # If no suitable blocks are found, return an empty list
        return []


    def find_contiguous_channels(self, channels, num_channels):
        """
        Find all possible contiguous blocks of num_channels in a list of channels.
        """
        channels = sorted(channels)
        contiguous_blocks = []
        
        for i in range(len(channels) - num_channels + 1):
            if channels[i + num_channels - 1] - channels[i] == num_channels - 1:
                contiguous_blocks.append(channels[i:i + num_channels])
    
        return contiguous_blocks


    def get_free_resources(self):
        if self.protocol == 3:
                    # Step 2: Process the received RS information
            for (slot, channel), received_status in self.RS_dict .items():
                                # Apply CRA6G logic for updating RS status
                if received_status == self.H:
                    self.resource_grid[slot][channel] = False
                

        free_resources = {}
        for slot, channels in enumerate(self.resource_grid):
            free_channels = [ch for ch, used in enumerate(channels) if not used]
            if free_channels:
                free_resources[slot] = free_channels
        return free_resources
    
    def get_all_resources(self):
        """
        Returns a list of all possible (slot, channel) pairs in the resource grid.
        """
        all_resources = []
        for slot in range(self.total_slots):
            for channel in range(self.channels_per_slot):
                all_resources.append((slot, channel))
        return all_resources



 
    

    def update_resources(self, slot, channels, used):
        pass
        for ch in channels:
            self.resource_grid[slot][ch] = used
            if used:
                
                self.resource_last_used[(slot, ch)] = self.scheduler.current_time
            elif (slot, ch) in self.resource_last_used:
                del self.resource_last_used[(slot, ch)]
    

    def release_resources(self, resources):
        # Loop through each resource (slot, channel pair)
        
        for slot, channel in resources:
            if self.protocol == 3:
                self.RS_grid[slot][channel]= self.H
                self.RS_dict[(slot, channel)] = self.H
            # Mark the channel as available (False)
            self.resource_grid[slot][channel] = False
            # Remove the channel from resource_last_used if it's being tracked
            if (slot, channel) in self.resource_last_used:
                del self.resource_last_used[(slot, channel)]
                
    def release_resources_future(self, resources, received_time):
        # Loop through each resource (slot, channel pair)
        
        for slot, channel in resources:
            if self.protocol == 3:
                self.RS_grid[slot][channel]= self.L
                self.RS_dict[(slot, channel)] = self.L
                self.RS_last_update[(slot, channel)] = received_time

    

    def release_unused_resources(self, current_time, rri):
        to_release = []
        for resource, last_used in self.resource_last_used.items():    
            
            if current_time - last_used >= self.ava_hold* rri:
                to_release.append(resource)
        for resource in to_release:
            slot, channel = resource
            self.update_resources(slot, [channel], False)




    def create_RS_grid(self):
        """
        Initialize the RS grid with all values set to 0 (free resources).
        Also initialize a distance grid with all distances set to 0.
        """
        # RS grid initialized with H (default resource state)
        rs_grid = [[self.H for _ in range(self.channels_per_slot)] for _ in range(self.total_slots)]
    
        # Distance grid initialized with 0 for all resources
        distance_grid = [[0 for _ in range(self.channels_per_slot)] for _ in range(self.total_slots)]
    
        # Return both the RS grid and the distance grid
        return rs_grid, distance_grid


    def create_RS_dict(self, RS_grid, Distance_grid):
        """
        Initialize the RS dictionary from the RS grid.
        Maps (slot, channel) to the status value (initially all set to 0).
        """
        RS_dict = {}
        Distance_dict = {}
        for slot in range(len(RS_grid)):
            for channel in range(len(RS_grid[slot])):
                RS_dict[(slot, channel)] = RS_grid[slot][channel]
                Distance_dict[(slot, channel)] =  Distance_grid[slot][channel]
        return RS_dict, Distance_dict


    def update_resource(self, slot, channel, status, distance, received_time):
        """Helper function to update RS and Distance grids."""
        self.RS_last_update[(slot, channel)] = received_time
        self.RS_grid[slot][channel] = status
        self.RS_dict[(slot, channel)] = status
        self.Distance_grid[slot][channel] = distance
        self.Distance_dict[(slot, channel)] = distance
    

    def update_recived_RS_grid(self, received_time, received_RS):
        """
        Updates the resource status grid (RS_grid) and distance grid (Distance_grid)
        based on received status and distance from neighbors.
        """


        #print("received_RS",received_RS)

        # Step 2: Process the received RS information
        for (slot, channel), rs_data in received_RS.items():
            received_status = rs_data['status']
            received_distance = rs_data['distance']
            # print(f"Slot: {slot}, Channel: {channel}, Status: {received_status}, Distance: {received_distance}")
            # Ensure the resource is within bounds
            if 0 <= slot < self.total_slots and 0 <= channel < self.channels_per_slot:
                current_status = self.RS_grid[slot][channel]
    
                # Update based on received status
                if received_status == 'DC':
                    self.update_resource(slot, channel, 1, received_distance, received_time)
                elif received_status == 'DF' and current_status in [self.H, self.L]:
                     self.update_resource(slot, channel, self.L, received_distance, received_time)
                elif received_status == 1 and current_status in [self.H, self.L]:
                     self.update_resource(slot, channel, 2, received_distance, received_time)
                # elif received_status == self.L and current_status in [self.H, self.L]:
                #      self.update_resource(slot, channel, self.L, received_distance, received_time)
                    
                elif received_status == 2 and current_status in [self.H, self.L]:
                     self.update_resource(slot, channel, self.H, received_distance, received_time)
    
        # Expiration logic for resources
        for (slot, channel), last_update_time in list(self.RS_last_update.items()):
            if (received_time - last_update_time) > self.rri_RS_factor * self.rri:
                # Reset resource to default state
                self.RS_grid[slot][channel] = self.H
                self.RS_dict[(slot, channel)] = self.H
                self.Distance_grid[slot][channel] = 0
                self.Distance_dict[(slot, channel)] = 0
                del self.RS_last_update[(slot, channel)]  # Remove from last update tracking
    
        # # Debugging output for specific node
        # if self.node.node_id == 10:
        #    # print("received_RS",received_RS)
        #     print("after RS_grid:", self.RS_grid)
        #     print("after Distance_grid:", self.Distance_grid)


    def update_RS_grid(self, sender_id, sender_resources,sender_future_resources, received_time, received_RS, received_flag):
        """
        Update the RS grid based on the sender's resources and the received RS information.
        Resources marked in Step 1 should not be updated in Step 2 until the RRI has elapsed.
    
        Parameters:
            - `sender_id`: The ID of the vehicle sending the packet.
            - `sender_resources`: List of (slot, channel) pairs used by the sender.
            - `received_time`: Current time for tracking resource updates.
            - `received_RS`: A dictionary with (slot, channel) as keys and status values:
                - 0: Free
                - 1: Occupied by a one-hop neighbor (direct neighbor)
                - 2: Occupied by a two-hop neighbor
                - 'H': High-priority available resource
                - 'L': Low-priority available resource
                
        """
        # Ensure updated_in_step1 is initialized and clean expired entries

        # if self.node.node_id=='veh487':
        #     print( "befor node_id", self.node.node_id, sender_id,   sender_resources, received_time)  
        #     print(  self.RS_grid) 
        
    

                
                # Step 1: Mark the sender's resources as occupied by a one-hop neighbor (status = H)
        if received_flag== 0:
                    # Step 1: Mark the sender's resources as occupied by a one-hop neighbor (status = 1)
            for slot, channel in sender_resources:
                
                if 0 <= slot < self.total_slots and 0 <= channel < self.channels_per_slot:
                    self.RS_grid[slot][channel] = 1
                    self.RS_dict[(slot, channel)] = 1
                    self.Distance_grid[slot][channel] = 0
                    self.Distance_dict[(slot, channel)] = 0
                    self.updated_in_step1[(slot, channel)] = received_time  # Track the resource with its timestamp
                    
            for slot, channel in sender_future_resources:
                if 0 <= slot < self.total_slots and 0 <= channel < self.channels_per_slot:
                    current_status = self.RS_grid[slot][channel]
                    if current_status == self.H or current_status == self.L :
                        self.RS_grid[slot][channel] = self.H
                        self.RS_dict[(slot, channel)] = self.H
                        self.Distance_grid[slot][channel] = 0
                        self.Distance_dict[(slot, channel)] = 0
                        self.updated_in_step1[(slot, channel)] = received_time  # Track the resource with its timestamp
     
            
    def get_free_resources_from_RS_grid_1(self):
        """
        Extract free resources (status = 0) from the RS_grid.
        
        Returns:
            A dictionary where keys are slots and values are lists of free channels.
        """
        free_resources = {}
        for slot, channels in enumerate(self.RS_grid):
            free_channels = [ch for ch, status in enumerate(channels) if status == self.H or status  == self.L]  # Free resources have status 0
            if free_channels:
                free_resources[slot] = free_channels
        return free_resources

    
    def get_free_resources_from_RS_grid(self):
        """
        Extract free resources (status = H or L) from the RS_grid.
        
        Returns:
            A dictionary where keys are slots and values are lists of tuples (channel, weight),
            where weight is based on the resource status (e.g., H=1.0, L=0.5).
        """
        free_resources = {}
        for slot, channels in enumerate(self.RS_grid):
            weighted_channels = []
            for ch, status in enumerate(channels):
                if status == self.H:
                    weighted_channels.append((ch, self.H))  # Assign higher weight to H
                elif status == self.L:
                    weighted_channels.append((ch, self.L))  # Assign lower weight to L
            if weighted_channels:
                #print("weighted_channels", weighted_channels)
                free_resources[slot] = weighted_channels
            
        return free_resources
    
  

    # def select_contiguous_resources_from_RS_grid(self, num_channels):
    #     """
    #     Select a free contiguous block of resources from RS_grid, considering weights,
    #     across all slots without prioritizing specific slots.
    #     """
    #     # Get free resources with weights from RS_grid
    #     free_resources = self.get_free_resources_from_RS_grid()
    #     all_contiguous_blocks = []  # To store all contiguous blocks with weights
    #     # if self.node.node_id == 10:
    #     #     print("node_id", self.node.node_id, free_resources)
    
    #     # Iterate through free resources to find contiguous blocks
    #     for slot, channels_with_weights in free_resources.items():
    #         if not channels_with_weights:
    #             continue
    
    #         # Separate channels and weights
    #         channels, weights = zip(*channels_with_weights)
    
    #         # Find all contiguous blocks of the required size
    #         contiguous_blocks = self.find_contiguous_channels(channels, num_channels)
    #         # if self.node.node_id == 10:
    #         #     print("node_id", self.node.node_id, channels,weights, contiguous_blocks)
    
    #         # Calculate weights for each block
    #         for block in contiguous_blocks:
    #             block_weight = sum(
    #                 weights[channels.index(ch)] for ch in block
    #             )
    #             all_contiguous_blocks.append((slot, block, block_weight))
    #     if self.node.node_id == 10:        
    #         if not all_contiguous_blocks:
    #             print(f"[ERROR] No contiguous blocks found at node {self.node.node_id}. Check RS grid and num_channels.")
    #             print(f"Free resources: {free_resources}")
    #             print(f"RS Grid: {self.RS_grid}")  # Print the current RS grid state
    #             return []
    #     if self.node.node_id == 10:
    #     # Ensure all_contiguous_blocks is not modified before max()
    #         print(f"Before max(), all_contiguous_blocks: {all_contiguous_blocks}")
        
    #     # Find the maximum block weight
    #     max_block_weight = max(block[2] for block in all_contiguous_blocks)
    #     if self.node.node_id == 10:
    #         print(f"Max block weight: {max_block_weight}")
        
    #     # Continue as normal
    #     best_blocks = [(block[0], block[1]) for block in all_contiguous_blocks if block[2] == max_block_weight]
    #     if self.node.node_id == 10:
    #         if not best_blocks:
    #             print("[ERROR] No best blocks found, but max_block_weight exists. Something is wrong!")
        
    #     selected_slot, selected_block = random.choice(best_blocks)
    #     result = [(selected_slot, ch) for ch in selected_block if ch in [x[0] for x in free_resources[selected_slot]]]
        
    #     return result

    def select_contiguous_resources_from_RS_grid(self, num_channels):
        free_resources = self.get_free_resources_from_RS_grid()
        all_contiguous_blocks = []  # Store all possible contiguous blocks with weights
    
        # if self.node.node_id == 152:
        #     print(f"Node {self.node.node_id} - Available Free Resources: {free_resources}")
    
        for slot, channels_with_weights in free_resources.items():
            if not channels_with_weights:
                continue
    
            # Separate channels and weights
            channels, weights = zip(*channels_with_weights)
    
            # Find all contiguous blocks of the required size
            contiguous_blocks = self.find_contiguous_channels(channels, num_channels)
    
            # Store blocks with weights
            for block in contiguous_blocks:
                block_weight = sum(weights[channels.index(ch)] for ch in block)
                all_contiguous_blocks.append((slot, block, block_weight))
    
        # if self.node.node_id == 152:
        #     print(f"Node {self.node.node_id} - All Contiguous Blocks: {all_contiguous_blocks}")
    
        # Prevent max() from failing on empty list
        if not all_contiguous_blocks:
            print(f"[ERROR] Node {self.node.node_id}: No contiguous blocks found!")
            return []
    
        max_block_weight = max(block[2] for block in all_contiguous_blocks)
    
        # if self.node.node_id == 152:
        #     print(f"Node {self.node.node_id} - Max Block Weight: {max_block_weight}")
    
        # Select the best blocks
        best_blocks = [(block[0], block[1]) for block in all_contiguous_blocks if block[2] == max_block_weight]
        
        # if not best_blocks:
        #     print(f"[ERROR] Node {self.node.node_id}: No best blocks found after max filtering!")
        #     return []
    
        selected_slot, selected_block = random.choice(best_blocks)
    
        result = [(selected_slot, ch) for ch in selected_block if ch in [x[0] for x in free_resources[selected_slot]]]
    
        # if self.node.node_id == 152:
        #     print(f"Node {self.node.node_id} - Final Selected Resources: {result}")
    
        return result


    def find_contiguous_channels(self, channels, num_channels):
        """
        Find all possible contiguous blocks of num_channels in a list of channels.
        """
        channels = sorted(channels)
        contiguous_blocks = []
        
        for i in range(len(channels) - num_channels + 1):
            if channels[i + num_channels - 1] - channels[i] == num_channels - 1:
                contiguous_blocks.append(channels[i:i + num_channels])
        
        return contiguous_blocks
    
    def release_RS_grid(self, sender_resources):

        for slot, channel in sender_resources:
            if 0 <= slot < self.total_slots and 0 <= channel < self.channels_per_slot:
                self.RS_grid[slot][channel] = self.H
                self.RS_dict[(slot, channel)] = self.H

