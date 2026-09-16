# helpers.py

import random
import logging
from collections import defaultdict

import math
import csv
from collections import Counter


def calculate_desired_signal(P_t, K_0, distance, alpha):
    """Calculate the desired signal based on distance and path loss."""
    return P_t * K_0 * ((distance + 1) ** -alpha)

def calculate_interference(P_t, K_0, receiving_node, all_nodes, transmitting_node, alpha):
    """Calculate the total interference from other nodes."""
    interference = 0
    for node in all_nodes:
        if node.node_id != transmitting_node.node_id and node.node_id != receiving_node.node_id:
            d_kj = math.sqrt(
                (receiving_node.position[0] - node.position[0]) ** 2 +
                (receiving_node.position[1] - node.position[1]) ** 2
            )
            interference += P_t * K_0 * ((d_kj + 1) ** -alpha)
    return interference

def calculate_distance(position1, position2):
    """Calculate the Euclidean distance between two positions."""
    return math.sqrt((position1[0] - position2[0]) ** 2 + (position1[1] - position2[1]) ** 2)




# helpers.py

import csv
from collections import defaultdict

def group_messages_by_time(receive_buffer):
    """Group received messages by their receive time."""
    messages_by_time = defaultdict(list)
    for receive_time, packet, resources in receive_buffer:
        messages_by_time[receive_time].append((packet, resources))
    return messages_by_time

def group_messages_by_resources(messages):
    """Group messages based on overlapping resources."""
    msg_groups = []
    for packet_i, resources_i in messages:
        resources_i_set = set(resources_i)
        overlapping_groups = []

        # Find overlapping groups of resources
        for msg_group in msg_groups:
            if not msg_group['resources'].isdisjoint(resources_i_set):
                overlapping_groups.append(msg_group)

        if overlapping_groups:
            combined_group = {'messages': [(packet_i, resources_i)], 'resources': resources_i_set}
            for group in overlapping_groups:
                combined_group['messages'].extend(group['messages'])
                combined_group['resources'].update(group['resources'])
            msg_groups = [group for group in msg_groups if group not in overlapping_groups]
            msg_groups.append(combined_group)
        else:
            msg_groups.append({'messages': [(packet_i, resources_i)], 'resources': resources_i_set})

    return msg_groups

def calculate_sinr_for_messages(node, sender_nodes, messages_in_group):
    """Calculate SINR and RSSI for each message in a group."""
    sinr_values = []
    for packet, resources in messages_in_group:
        sender_id = packet['sender']
        other_nodes = [node for sid, node in sender_nodes.items() if sid != sender_id]
        sinr, rssi = node.mac_layer.calculate_sinr(node, sender_nodes[sender_id], other_nodes)
        sinr_values.append((sinr, rssi, packet, resources))
    return sinr_values

def write_sinr_to_csv(filename, sinr_data, receive_time, node, sender_nodes):
    """Write SINR and RSSI values to a CSV file."""
    if sinr_data:
        with open(filename, 'a', newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=['timestamp', 'sender_id', 'receiver_id', 'packet_id', 'distance', 'success'])
            for sinr, rssi, packet, resources in sinr_data:
                success = 1 if sinr >= node.mac_layer.SINR_threshold_dB and rssi > node.mac_layer.RSSI_Margin_dB else 0
                writer.writerow({
                    'timestamp': receive_time,
                    'sender_id': packet['sender'],
                    'receiver_id': node.node_id,
                    'packet_id': packet['data'],
                    'distance': node.mac_layer.calculate_distance(node.position, sender_nodes[packet['sender']].position),
                    'success': success
                })





def check_future_resources(future_resource, free_resources):
    """
    Check if the future resource is still available in the free resource list.

    Parameters:
        future_resource (iterable): An iterable of (slot, channel) pairs representing the future resource.
        free_resources (dict): A dictionary where keys are slots and values are lists of free channels.

    Returns:
        tuple: A tuple (is_available, overlapped_resources) where:
            - is_available (bool): True if every (slot, channel) in future_resource is found in free_resources.
            - overlapped_resources (list): A list of (slot, channel) pairs that are not available.
    """
    overlapped_resources = [
        (slot, channel)
        for slot, channel in future_resource
        if slot not in free_resources or channel not in free_resources[slot]
    ]
    is_available = len(overlapped_resources) == 0
    return is_available, overlapped_resources

def update_resource_tracker(node, current_resources):
    """Update the resource usage tracker and detect conflicts."""
    for res in current_resources:
        if res in node.resource_usage_tracker:
            node.resource_usage_tracker[res].add(node.node_id)
        else:
            node.resource_usage_tracker[res] = {node.node_id}

        if len(node.resource_usage_tracker[res]) > 1:
            logger.warning(f"Conflict detected during transmission from node {node.node_id} on resource {res} with nodes {node.resource_usage_tracker[res]}")




def check_resource_overlap(current_resources, collided_resources):
    """Check for overlap between current and collided resources."""
    overlap_count = 0
    id_s = []
    res_s = []

    for resource, attached_id in collided_resources:
        overlap = [res for res in current_resources if res == resource]
        if overlap:
            overlap_count += 1
            id_s.append(attached_id)
            res_s.append(resource)

    return overlap_count, id_s, res_s

def calculate_collision_density(received_collision_count, neighbor_table_length, num_channels):
    """Calculate collision density based on received collisions and neighbor table size."""
    if neighbor_table_length > 0:
        return received_collision_count / (neighbor_table_length * num_channels)
    return 0

def get_most_common_id(id_s):
    """Identify the most common attached ID, excluding -1."""
    filtered_ids = [x for x in id_s if x != -1]
    ids_counts = Counter(filtered_ids)
    if ids_counts:
        return ids_counts.most_common(1)[0][0]
    return -1


def convert_RS_to_dict(RS_grid):
    RS_dict = {}
    for slot in range(len(RS_grid)):
        for channel in range(len(RS_grid[slot])):
            status = RS_grid[slot][channel]
            if status != 0:  # Only include non-zero (used) resources
                RS_dict[(slot, channel)] = status
    return RS_dict

def convert_dict_to_RS(RS_dict, total_slots, channels_per_slot):
    RS_grid = [[0 for _ in range(channels_per_slot)] for _ in range(total_slots)]
    for (slot, channel), status in RS_dict.items():
        if 0 <= slot < total_slots and 0 <= channel < channels_per_slot:
            RS_grid[slot][channel] = status
    return RS_grid

