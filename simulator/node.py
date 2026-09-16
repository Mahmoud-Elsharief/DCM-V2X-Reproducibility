import math
from event import Event
from mac import MACLayer
from collections import defaultdict
import random 

class Node:
    def __init__(self, node_id, position, route, scheduler, bandwidth_mhz, numerology, rri, L, H, pk, num_channels, sensing_window_duration, available_percentage, periodic, protocol, initial_P_s_dB, P_t_dB, K_0_dB, alpha, SINR_threshold_dB, filename):
        self.node_id = node_id
        self.position = list(position)  # Using list to allow mutable positions
        self.route = route  # Route dictionary
        first_route = next(iter(route.values()), {})
        self.street_id = first_route.get('street_id')
        self.road_id = first_route.get('road_id')
        self.edge_id = first_route.get('edge_id')
        self.lane_id = first_route.get('lane_id')
        self.heading = first_route.get('heading')
        self.scheduler = scheduler
        self.mac_layer = MACLayer(self, bandwidth_mhz, numerology, rri, L, H, pk, num_channels, sensing_window_duration, available_percentage, periodic, protocol, initial_P_s_dB, P_t_dB, K_0_dB, alpha, SINR_threshold_dB, filename)
        self.speed = 0  # Initial speed is 0
        self.rri = rri
        self.initial_P_s_dB = initial_P_s_dB
        self.P_t_dB = P_t_dB
        self.K_0_dB = K_0_dB
        self.alpha = alpha
        self.sensing_window_duration = sensing_window_duration
        self.sent_packets = 0
        self.received_packets_from_neighbors = 0
        self.reception_times = []
        self.successful_transmissions = 0
        self.total_resources_used = 0
        self.count_receive=0
        self.firstsend=0
        self.firstreceive=1
        self.stopnode=False
        
        # Run-seed-aware, per-node initial offset.  Avoid mutating the global
        # RNG so independent Monte-Carlo seeds are reproducible and matched.
        run_seed = int(__import__("os").environ.get("NRV2X_RUN_SEED", "1"))
        init_rng = random.Random(f"{run_seed}|{node_id}|initial")
        # Randomize the first periodic packet only within one RRI.  Using a
        # hard-coded 0--1 s range makes short tests invalid and delays many
        # UEs by up to ten RRIs when RRI=100 ms.
        slot_s = 0.001 / (2 ** int(numerology))
        phase_slots = max(1, int(round(float(rri) / slot_s)))
        self.initial_time = slot_s * init_rng.randrange(phase_slots)
        self.next_transmission_time = self.scheduler.current_time + self.initial_time
        

        self.neighbors = []
        self.resource_usage_tracker = defaultdict(set)
        self.sending_times_resources = {}
        self.transmission_event = None
        self.reevaluation_event = None
        self.packet_counter = 0
        # Packet-accounting instrumentation. A packet is counted as generated
        # the first time its scheduled transmission opportunity is reached.
        # Rescheduling the same packet after re-evaluation/collision recovery
        # does not create a new application packet.
        self.generated_packet_ids = set()
        self.packet_queue = []
        self.application_packets_generated = 0
        self.tx_opportunities = 0
        self.actual_transmissions = 0
        self.deferred_transmissions = 0
        self.dropped_packets = 0
        self.defer_delays = []
        self.received_packets = defaultdict(lambda: defaultdict(list))
        self.schedule_next_sensing()
        # Reconstructed NR/SORA decouple application generation from the radio
        # scheduler. Legacy profiles preserve the original coupled behavior.
        if self.mac_layer.common_core_enabled():
            self.schedule_application_generation_after_initial_time()
        else:
            self.schedule_transmission_after_initial_time()
        self.road_length=2000
        
        # Initialize max_distance using calculate_distance
        self.max_distance = self.calculate_distance(initial_P_s_dB, P_t_dB, K_0_dB, alpha)

        # Initialize neighbor table
        self.neighbor_table = {}
        
        # Schedule periodic neighbor table maintenance
        self.schedule_neighbor_table_maintenance()
        # Schedule periodic neighbor table printing
       # self.schedule_neighbor_table_printing()
           # Initialize PRAT with a timeout for stale entries (in seconds)
        self.prat_timeout = 1*self.rri
        self.prat = {}
       
        self.initialize_prat(self.mac_layer.resource_allocation.get_all_resources())
        self.schedule_periodic_cleanup()
       # print(self.node_id, self.position)
        
    def schedule_transmission_after_initial_time(self):
        """
        Schedule the first transmission event after the initial time has passed.
        """
        # Schedule the first transmission after initial_time
       
        event = Event(self.next_transmission_time, self.schedule_next_transmission)
        self.scheduler.schedule_event(self.next_transmission_time, event)

    def schedule_application_generation_after_initial_time(self):
        """Start an application source that is independent of resource changes."""
        t = float(self.next_transmission_time)
        self.scheduler.schedule_event(t, Event(t, self.generate_application_packet, priority=-10))

    def generate_application_packet(self):
        """Generate one offered packet every RRI, regardless of MAC deferrals."""
        if self.stopnode:
            return
        packet = {
            "data": f"Packet from {self.node_id}_{self.packet_counter}",
            "generated_time": float(self.scheduler.current_time),
        }
        self.packet_counter += 1
        self.generated_packet_ids.add(packet["data"])
        self.application_packets_generated += 1
        self.packet_queue.append(packet)

        # Only one head-of-line radio event may be active.
        if self.transmission_event is None or self.transmission_event.event_id not in self.scheduler.event_map:
            self.schedule_next_transmission()

        next_t = round(float(self.scheduler.current_time + self.rri), 9)
        if next_t < self.scheduler.simulation_time:
            self.scheduler.schedule_event(next_t, Event(next_t, self.generate_application_packet, priority=-10))

    def initialize_prat(self, resource_set):
        """
        Initialize the PRAT for this node with empty entries for each resource.
        """
        for r in resource_set:
            self.prat[r] = {
                'U': [],  # List of tuples (node_id, position, timestamp)
                'A': [],  # List of tuples (node_id, position, probability, timestamp)
                # 'AT': [],  # List of tuples (node_id, position, probability, timestamp)
                
                'highest_u_id': None,  # Highest node_id in U
                'highest_a_id': None   # Highest node_id in A
            }
      




    def clean_up_prat(self):
        """
        Remove stale entries from the PRAT that have not been updated within the timeout period.
        """
        current_time = self.scheduler.current_time
    
        for resource in self.prat.keys():
            # Clean U_r_i by filtering out expired entries
            self.prat[resource]['U'] = [
                u_data for u_data in self.prat[resource]['U'] if current_time - u_data[2] <= self.prat_timeout
            ]
    
            # Clean A_r_i by filtering out expired entries
            self.prat[resource]['A'] = [
                a_data for a_data in self.prat[resource]['A'] if current_time - a_data[3] <= self.prat_timeout
            ]
            
            # # Clean A_r_i by filtering out expired entries
            # self.prat[resource]['AT'] = [
            #     a_data for a_data in self.prat[resource]['AT'] if current_time - a_data[3] <= self.prat_timeout
            # ]
    
        # if self.node_id==10:
            
        #     #print(self.prat )
        #     self.print_prat()


    def schedule_periodic_cleanup(self):
        """
        Schedule the periodic cleanup of stale PRAT entries.
        """

        
        cleanup_interval =self.rri  # Run cleanup every second
        self.scheduler.schedule_recurring_event(0, cleanup_interval, self.clean_up_prat)
        
    def print_prat(self):
        """
        Print the PRAT for this node in a tabular format.
        """
        print(f"PRAT for Node {self.node_id}:")
        print("="*100)
        print(f"{'Resource':<20}{'Type':<10}{'Node ID':<10}{'Position':<20}{'Probability':<15}{'Timestamp':<}{'Highest ID':<15}")
        print("-"*100)
        
        for resource, entries in self.prat.items():
            # Print the highest_u_id and highest_a_id
            highest_u_id = entries.get('highest_u_id', 'None')
            highest_a_id = entries.get('highest_a_id', 'None')
            
            u_data_str = "None"
            if entries['U']:
                for u_data in entries['U']:
                    print(f"{str(resource):<20}{'U':<10}{u_data[0]:<10}{str(u_data[1]):<20}{'N/A':<15}{u_data[2]:<20}{highest_u_id:<15}")
                    
            a_data_str = "None"
            if entries['A']:
                for a_data in entries['A']:
                    print(f"{str(resource):<20}{'A':<10}{a_data[0]:<10}{str(a_data[1]):<20}{a_data[2]:<15}{a_data[3]:<20}{highest_a_id:<15}")
        
        print("="*100)





                    
   
    def schedule_neighbor_table_printing(self):
        print_event = Event(self.scheduler.current_time + 1, self.print_neighbor_table)
        self.scheduler.schedule_event(self.scheduler.current_time + 1, print_event)
        
    def update_neighbor_table(self, sender_id, resources, receive_time):

        # Check if the neighbor (sender) is already in the neighbor table
        if sender_id in self.neighbor_table:

            # Delete the old data
            del self.neighbor_table[sender_id]

        # Calculate the distance between the current node and the sender
        distance = self.mac_layer.calculate_distance(self.position, self.scheduler.node_manager.nodes[sender_id].position)
        
        # Now add the new data to the neighbor table
        self.neighbor_table[sender_id] = {
            'resources': resources,
            'expected_resources': 'idle',  # Setting a default value for expected resources
            'last_received_time': receive_time,
            'distance': distance
        }

    def find_neighbors_above_distance(self, new_distance):
        # Create a list to store neighbors that are beyond the specified distance
        distant_neighbors = []
        
        # Iterate over the neighbors in the neighbor table
        for neighbor_id, neighbor_data in self.neighbor_table.items():
            # Check if the neighbor's distance is greater than the specified distance
            if neighbor_data['distance'] > new_distance:
                distant_neighbors.append((neighbor_id, neighbor_data))
        
        # Return the list of distant neighbors
        return distant_neighbors
    


    def maintain_neighbor_table(self):
        
        current_time = self.scheduler.current_time
        stale_neighbors = [neighbor for neighbor, data in self.neighbor_table.items() if current_time - data['last_received_time'] > self.rri ]
        for neighbor in stale_neighbors:
            del self.neighbor_table[neighbor]
        # if self.node_id==100:
        #     print(self.scheduler.current_time, "maintain_neighbor_table", len(self.neighbor_table))
        

    def schedule_neighbor_table_maintenance(self):
        # maintenance_event = Event(self.scheduler.current_time + 1, self.maintain_neighbor_table)
        # self.scheduler.schedule_event(self.scheduler.current_time + 1, maintenance_event)
            
        updating_interval =self.rri  # Run cleanup every second
        self.scheduler.schedule_recurring_event(0, updating_interval, self.maintain_neighbor_table)
        
    def print_neighbor_table(self):
        print(f"Neighbor Table for Node {self.node_id}:")
        print("Neighbor ID | distance | Resources | Expected Resources | Last Received Time")
        for neighbor_id, data in self.neighbor_table.items():
            print(f"{neighbor_id} | {data['distance']}| {data['resources']} | {data['expected_resources']} | {data['last_received_time']}  ")

        # Reschedule the next print event
        self.schedule_neighbor_table_printing()
        
        

    def move(self):
       # logger.debug(f"Node {self.node_id} is moving at time {self.scheduler.current_time}")
        # Update position and speed based on the current time step
        #print(self.node_id, self.scheduler.current_time) 
        route_time = round(float(self.scheduler.current_time), 9)
        if route_time in self.route:
            #print(self.node_id, self.scheduler.current_time, self.route)
            self.position[0] = self.route[route_time]['position'][0]
            self.position[1] = self.route[route_time]['position'][1]
            self.speed = self.route[route_time]['speed']

        self.calculate_neighbors(self.scheduler.node_manager.nodes.values(), self.max_distance)  # Recalculate neighbors after movement
        self.schedule_next_move()

    def schedule_next_move(self):
        interval = getattr(self.scheduler.node_manager, 'mobility_update_s', 1.0)
        next_time = round(float(self.scheduler.current_time + interval), 9)
        move_event = Event(next_time, self.move, priority=-100)
        self.scheduler.schedule_event(next_time, move_event)

    def start_moving(self, speed):
        self.speed = speed
        self.schedule_next_move()

    def update_position(self, new_position, new_speed, road_id=None, heading=None,
                        street_id=None, edge_id=None, lane_id=None):

        self.position = new_position
        self.speed = new_speed
        if road_id is not None:
            self.road_id = road_id
        if street_id is not None:
            self.street_id = street_id
        if edge_id is not None:
            self.edge_id = edge_id
        if lane_id is not None:
            self.lane_id = lane_id
        if heading is not None:
            self.heading = heading


    def print_neighbors(self):
        neighbor_ids = [neighbor.node_id for neighbor in self.neighbors]
        print(f"Node {self.node_id} neighbors: {neighbor_ids}")

    def print_position(self):
        print(f"Node {self.node_id} position: {self.position}")

    # Removed 'self.' from 'max_distance' parameter in this method definition
    def calculate_neighbors(self, all_nodes, max_distance):
        self.neighbors = []
        for node in all_nodes:
            if node.node_id != self.node_id:
                distance = math.sqrt((self.position[0] - node.position[0]) ** 2 + (self.position[1] - node.position[1]) ** 2)
                if distance <= max_distance:
                    self.neighbors.append(node)

    def send_packet(self, packet):
        """Serve a packet at its scheduled resource opportunity."""
        if self.stopnode:
            return

        # Legacy profiles keep the original coupled source/scheduler semantics.
        if not self.mac_layer.common_core_enabled():
            packet_id = packet.get("data")
            if packet_id not in self.generated_packet_ids:
                self.generated_packet_ids.add(packet_id)
                self.application_packets_generated += 1
                packet["generated_time"] = float(self.scheduler.current_time)
            self.tx_opportunities += 1
            result = self.mac_layer.send_packet(packet) or {"status": "dropped", "reason": "unknown"}
            status = result.get("status", "dropped")
            if status == "transmitted":
                self.sent_packets += 1
                self.actual_transmissions += 1
                self.defer_delays.append(0.0)
                self.scheduler.log_transmission(self.scheduler.current_time, self.node_id, result.get("resources", self.mac_layer.current_resources))
                self.schedule_next_transmission()
            elif status == "deferred":
                self.deferred_transmissions += 1
                self.schedule_next_transmission_based_on_new_resources(packet=packet)
            else:
                self.dropped_packets += 1
                self.schedule_next_transmission()
            return

        # Reconstructed profiles consume only the current head-of-line packet.
        if not self.packet_queue:
            self.transmission_event = None
            return
        if packet is not self.packet_queue[0] and packet.get("data") != self.packet_queue[0].get("data"):
            # Stale canceled/rescheduled event.
            return

        self.tx_opportunities += 1
        result = self.mac_layer.send_packet(packet) or {"status": "dropped", "reason": "unknown"}
        status = result.get("status", "dropped")

        if status == "transmitted":
            self.sent_packets += 1
            self.actual_transmissions += 1
            delay = float(self.scheduler.current_time) - float(packet.get("generated_time", self.scheduler.current_time))
            self.defer_delays.append(max(0.0, delay))
            self.scheduler.log_transmission(self.scheduler.current_time, self.node_id, result.get("resources", self.mac_layer.current_resources))
            self.packet_queue.pop(0)
            self.transmission_event = None
            if self.packet_queue:
                self.schedule_next_transmission()
        elif status == "deferred":
            self.deferred_transmissions += 1
            self.schedule_next_transmission_based_on_new_resources(packet=packet)
        else:
            self.dropped_packets += 1
            self.packet_queue.pop(0)
            self.transmission_event = None
            if self.packet_queue:
                self.schedule_next_transmission()

    def receive_packet(self, packet, resources):
        if not self.stopnode:
            sender_id = packet['sender']
            dis = self.mac_layer.calculate_distance(self.position, self.scheduler.node_manager.nodes[sender_id].position)
            # if self.firstreceive:
            #     # Notify about the packet reception
            #     print(f"Node {self.node_id} received a packet from Node {sender_id} at time {self.scheduler.current_time}")
            #     self.firstreceive=0
        
           # if dis<= self.max_distance:
       
        self.mac_layer.receive_packet(packet, resources)

    def is_neighbor(self, sender_id):
        return any(neighbor.node_id == sender_id for neighbor in self.neighbors)

    def schedule_next_sensing(self):
        self.scheduler.schedule_event(self.scheduler.current_time + self.sensing_window_duration, Event(self.scheduler.current_time + self.sensing_window_duration, self.mac_layer.sensing_window))

    def _schedule_nr_core_reevaluation(self):
        if not self.mac_layer.common_core_enabled():
            return
        if not self.mac_layer.nr_needs_reevaluation:
            return
        if self.transmission_event is None:
            return
        # Release-16 re-evaluation timing: T3 = Tproc,1.  For numerology 0
        # this is 3 slots (3 ms); other numerologies use Table 8.1.4-2.
        lead = self.mac_layer.nr_selector.cfg.t3_s
        t = self.transmission_event.event_time - lead
        if t <= self.scheduler.current_time:
            return
        if self.reevaluation_event:
            self.scheduler.cancel_event(self.reevaluation_event)
        self.reevaluation_event = Event(t, self._run_nr_core_reevaluation)
        self.scheduler.schedule_event(t, self.reevaluation_event)

    def _run_nr_core_reevaluation(self):
        changed = self.mac_layer.nr_core_reevaluate_scheduled()
        self.reevaluation_event = None
        if changed:
            # Preserve the already-generated/scheduled packet while moving it
            # to the replacement resource.  The previous implementation
            # created a new packet here, silently discarding the old one.
            packet = None
            if self.transmission_event:
                if self.transmission_event.args:
                    packet = self.transmission_event.args[0]
                self.scheduler.cancel_event(self.transmission_event)
                self.sending_times_resources.pop(self.transmission_event.event_time, None)
            self.schedule_next_transmission_based_on_new_resources(packet=packet)

    def schedule_next_transmission(self):
        if self.transmission_event:
            self.scheduler.cancel_event(self.transmission_event)
            self.sending_times_resources.pop(self.transmission_event.event_time, None)
        slot_idx, channels = self.mac_layer.current_resources[0]
        frame_idx, subframe_idx, slot_idx, _ = self.mac_layer.resource_allocation.convert_back_to_detailed(slot_idx, channels)
        self.next_transmission_time = self.calculate_next_transmission_time(frame_idx, subframe_idx, slot_idx)

        if self.mac_layer.common_core_enabled():
            if not self.packet_queue:
                self.transmission_event = None
                return
            packet = self.packet_queue[0]
        else:
            packet = {"data": f"Packet from {self.node_id}_{self.packet_counter}"}
            self.packet_counter += 1

        event = Event(self.next_transmission_time, self.send_packet, packet)
        self.scheduler.schedule_event(self.next_transmission_time, event)
        self.transmission_event = event
        self.sending_times_resources[self.next_transmission_time] = self.mac_layer.current_resources
        if hasattr(self.mac_layer, "bind_scheduled_candidate"):
            self.mac_layer.bind_scheduled_candidate(self.next_transmission_time)
        self._schedule_nr_core_reevaluation()

    def calculate_next_transmission_time(self, frame_idx, subframe_idx, slot_idx):
        slot_duration = 1 / (2 ** self.mac_layer.numerology)
        self.next_transmission_time = ((frame_idx * 10) + (subframe_idx * 1) + (slot_idx * slot_duration))/1000
        #print(frame_idx, subframe_idx, slot_idx,self.next_transmission_time,self.scheduler.current_time )
        while self.next_transmission_time <= self.scheduler.current_time:
            self.next_transmission_time += self.rri
        return self.next_transmission_time

    def schedule_next_transmission_based_on_new_resources(self, packet=None):
        """Move the head-of-line packet to the new resource phase."""
        if self.transmission_event:
            self.scheduler.cancel_event(self.transmission_event)
            self.sending_times_resources.pop(self.transmission_event.event_time, None)
        slot_idx, channels = self.mac_layer.current_resources[0]
        frame_idx, subframe_idx, slot_idx, _ = self.mac_layer.resource_allocation.convert_back_to_detailed(slot_idx, channels)
        self.next_transmission_time = self.calculate_next_transmission_time(frame_idx, subframe_idx, slot_idx)

        if self.mac_layer.common_core_enabled():
            if packet is None:
                if not self.packet_queue:
                    self.transmission_event = None
                    return
                packet = self.packet_queue[0]
        elif packet is None:
            packet = {"data": f"Packet from {self.node_id}_{self.packet_counter}"}
            self.packet_counter += 1

        event = Event(self.next_transmission_time, self.send_packet, packet)
        self.scheduler.schedule_event(self.next_transmission_time, event)
        self.transmission_event = event
        self.sending_times_resources[self.next_transmission_time] = self.mac_layer.current_resources
        if hasattr(self.mac_layer, "bind_scheduled_candidate"):
            self.mac_layer.bind_scheduled_candidate(self.next_transmission_time)
        self._schedule_nr_core_reevaluation()

    def update_max_distance(self):
        self.max_distance = self.calculate_distance(self.initial_P_s_dB, self.P_t_dB, self.K_0_dB, self.alpha)
        self.calculate_neighbors(self.scheduler.node_manager.nodes.values(), self.max_distance)

    def calculate_distance(self, P_s_dB, P_t_dB, K_0_dB, alpha):
        P_t = (10 ** (P_t_dB / 10))
        P_s = (10 ** (P_s_dB / 10))
        K_0 = 10 ** (K_0_dB / 10)
        d_s = ((P_t * K_0) / P_s) ** (1 / alpha)
        return d_s

    def received_packets_from_node(self, sender_id):
        return sum(len(self.received_packets[sender_id][receiver_id]) for receiver_id in self.received_packets[sender_id])

    def calculate_pir(self):
        pir_list = {}
        for sender_id, receivers in self.received_packets.items():
            pir_list[sender_id] = {}
            for receiver_id, received_times in receivers.items():
                received_times = sorted(received_times)
                pir_intervals = [received_times[i] - received_times[i - 1] for i in range(1, len(received_times))]
                if pir_intervals:
                    pir_list[sender_id][receiver_id] = sum(pir_intervals) / len(pir_intervals)
            #print("sender_id",sender_id ,"pir_intervals",pir_intervals)
        return pir_list

    def calculate_metrics(self):
       
        num_neighbors = len(self.neighbors)
        total_packets_sent = self.sent_packets * num_neighbors
       # print("self.neighbors",self.neighbors)
       # print("num_neighbors", num_neighbors)
       # print("self.sent_packets",self.sent_packets , "self.successful_transmissions",  self.successful_transmissions)
        total_packets_received_by_neighbors = sum(neighbor.received_packets_from_node(self.node_id) for neighbor in self.neighbors)
        #print("total_packets_sent", total_packets_sent, "total_packets_received_by_neighbors", total_packets_received_by_neighbors)
        pdr = (total_packets_received_by_neighbors / total_packets_sent) * 100 if total_packets_sent else 0
        pir_list = self.calculate_pir()
        pir_values = [pir for sender_pir in pir_list.values() for pir in sender_pir.values()]
        pir = sum(pir_values) / len(pir_values) if pir_values else 0
        total_available_resources = len(self.mac_layer.resource_allocation.resource_grid) * self.mac_layer.resource_allocation.channels_per_slot
        resource_utilization = (self.total_resources_used / total_available_resources) * 100 if total_available_resources else 0
        return pdr, pir, resource_utilization
