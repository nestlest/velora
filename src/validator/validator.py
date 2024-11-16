"""
CommuneX example of a Text Validator Module

This module provides an example VeloraValidator class for validating text generated by modules in subnets.
The VeloraValidator retrieves module addresses from the subnet, prompts the modules to generate answers to a given question,
and scores the generated answers against the validator's own answers.

Classes:
    VeloraValidator: A class for validating text generated by modules in a subnet.

Functions:
    set_weights: Blockchain call to set weights for miners based on their scores.
    cut_to_max_allowed_weights: Cut the scores to the maximum allowed weights.
    extract_address: Extract an address from a string.
    get_subnet_netuid: Retrieve the network UID of the subnet.
    get_ip_port: Get the IP and port information from module addresses.

Constants:
    IP_REGEX: A regular expression pattern for matching IP addresses.
"""

import asyncio
import concurrent.futures
import json
import re
import time
from functools import partial
from datetime import timedelta, datetime, date

from communex.client import CommuneClient  # type: ignore
from communex.module.client import ModuleClient  # type: ignore
from communex.module.module import Module  # type: ignore
from communex.types import Ss58Address  # type: ignore
from substrateinterface import Keypair  # type: ignore

from ._config import ValidatorSettings
from utils.log import log
from utils.protocols import (HealthCheckSynapse, HealthCheckResponse,
                             PoolEventSynapse, PoolEventResponse,
                             SignalEventSynapse, SignalEventResponse,
                             PredictionSynapse, PredictionResponse,
                             class_dict)
from uniswap_fetcher_rs import UniswapFetcher

from communex._common import ComxSettings  # type: ignore

import random
import os
from dotenv import load_dotenv
import wandb

load_dotenv()

IP_REGEX = re.compile(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+")

EPS = 1e-10
START_TIMESTAMP = int(datetime(2021, 5, 4).timestamp())
DAY_SECONDS = 86400

def check_url_testnet(url: str):
    mainnet_urls = ComxSettings().NODE_URLS

    if url in mainnet_urls:
        return False
    return True

def set_weights(
    settings: ValidatorSettings,
    score_dict: dict[
        int, float
    ],  # implemented as a float score from 0 to 1, one being the best
    # you can implement your custom logic for scoring
    netuid: int,
    client: CommuneClient,
    key: Keypair,
) -> None:
    """
    Set weights for miners based on their scores.

    Args:
        score_dict: A dictionary mapping miner UIDs to their scores.
        netuid: The network UID.
        client: The CommuneX client.
        key: The keypair for signing transactions.
    """

    # you can replace with `max_allowed_weights` with the amount your subnet allows
    score_dict = cut_to_max_allowed_weights(score_dict, settings.max_allowed_weights)

    # Create a new dictionary to store the weighted scores
    weighted_scores: dict[int, int] = {}

    # Calculate the sum of all inverted scores
    scores = sum(score_dict.values())

    # process the scores into weights of type dict[int, int] 
    # Iterate over the items in the score_dict
    for uid, score in score_dict.items():
        # Calculate the normalized weight as an integer
        weight = int(score * 1000 / scores)

        # Add the weighted score to the new dictionary
        weighted_scores[uid] = weight


    # filter out 0 weights
    weighted_scores = {k: v for k, v in weighted_scores.items() if v != 0}

    uids = list(weighted_scores.keys())
    weights = list(weighted_scores.values())
    # send the blockchain call
    client.vote(key=key, uids=uids, weights=weights, netuid=netuid)

def cut_to_max_allowed_weights(
    score_dict: dict[int, float], max_allowed_weights: int
) -> dict[int, float]:
    """
    Cut the scores to the maximum allowed weights.

    Args:
        score_dict: A dictionary mapping miner UIDs to their scores.
        max_allowed_weights: The maximum allowed weights (default: 420).

    Returns:
        A dictionary mapping miner UIDs to their scores, where the scores have been cut to the maximum allowed weights.
    """
    # sort the score by highest to lowest
    sorted_scores = sorted(score_dict.items(), key=lambda x: x[1], reverse=True)

    # cut to max_allowed_weights
    cut_scores = sorted_scores[:max_allowed_weights]

    return dict(cut_scores)

def extract_address(string: str):
    """
    Extracts an address from a string.
    """
    return re.search(IP_REGEX, string)

def get_subnet_netuid(clinet: CommuneClient, subnet_name: str = "replace-with-your-subnet-name"):
    """
    Retrieve the network UID of the subnet.

    Args:
        client: The CommuneX client.
        subnet_name: The name of the subnet (default: "foo").

    Returns:
        The network UID of the subnet.

    Raises:
        ValueError: If the subnet is not found.
    """

    subnets = clinet.query_map_subnet_names()
    for netuid, name in subnets.items():
        if name == subnet_name:
            return netuid
    raise ValueError(f"Subnet {subnet_name} not found")

def get_ip_port(modules_adresses: dict[int, str]):
    """
    Get the IP and port information from module addresses.

    Args:
        modules_addresses: A dictionary mapping module IDs to their addresses.

    Returns:
        A dictionary mapping module IDs to their IP and port information.
    """

    filtered_addr = {id: extract_address(addr) for id, addr in modules_adresses.items()}
    ip_port = {
        id: x.group(0).split(":") for id, x in filtered_addr.items() if x is not None
    }
    return ip_port
class VeloraValidator(Module):
    """
    A class for validating text generated by modules in a subnet.

    Attributes:
        client: The CommuneClient instance used to interact with the subnet.
        key: The keypair used for authentication.
        netuid: The unique identifier of the subnet.
        val_model: The validation model used for scoring answers.
        call_timeout: The timeout value for module calls in seconds (default: 60).

    Methods:
        get_modules: Retrieve all module addresses from the subnet.
        _get_miner_prediction: Prompt a miner module to generate an answer to the given question.
        check_pool_event_accuracy: Score the generated answer against the validator's own answer.
        get_pool_event_synapse: Generate a prompt for the miner modules.
        validate_step: Perform a validation step by generating questions, prompting modules, and scoring answers.
        validation_loop: Run the validation loop continuously based on the provided settings.
    """

    def __init__(
        self,
        key: Keypair,
        netuid: int,
        client: CommuneClient,
        call_timeout: int = 60,
        wandb_on: int = False
    ) -> None:
        super().__init__()
        self.client = client
        self.key = key
        self.netuid = netuid
        self.val_model = "foo"
        self.call_timeout = call_timeout
        
        self.uniswap_fetcher_rs = UniswapFetcher(os.getenv('ETHEREUM_RPC_NODE_URL'))
        self.wandb_running = False
        if wandb_on:
            self.init_wandb()
        
    def __del__(self):
        if self.wandb_running:
            self.wandb_run.finish()
    
    def init_wandb(self):
        wandb_api_key = os.getenv("WANDB_API_KEY")
        if wandb_api_key is not None:
            log("Logging into wandb.")
            wandb.login(key=wandb_api_key)
        else:
            self.wandb_running = False
            log("WANDB_API_KEY not found in environment variables.")
            return
        
        self.wandb_run = None
        self.wandb_run_start = None
        if check_url_testnet(self.client.url):
            self.wandb_project_name = "velora-test"
        else:
            self.wandb_project_name = "velora"
        self.wandb_entity = "mltrev23"
        self.new_wandb_run()
        self.wandb_running = True

    def get_addresses(self, client: CommuneClient, netuid: int) -> dict[int, str]:
        """
        Retrieve all module addresses from the subnet.

        Args:
            client: The CommuneClient instance used to query the subnet.
            netuid: The unique identifier of the subnet.

        Returns:
            A dictionary mapping module IDs to their addresses.
        """

        # Makes a blockchain query for the miner addresses
        module_addreses = client.query_map_address(netuid)
        return module_addreses
    
    def retrieve_miner_information(self, velora_netuid):
        modules_adresses = self.get_addresses(self.client, velora_netuid)
        modules_keys = self.client.query_map_key(velora_netuid)
        val_ss58 = self.key.ss58_address
        if val_ss58 not in modules_keys.values():
            raise RuntimeError(f"validator key {val_ss58} is not registered in subnet")

        modules_info: dict[int, tuple[list[str], Ss58Address]] = {}

        modules_filtered_address = get_ip_port(modules_adresses)
        for module_id in modules_keys.keys():
            module_addr = modules_filtered_address.get(module_id, None)
            if not module_addr:
                continue
            modules_info[module_id] = (module_addr, modules_keys[module_id])
        return modules_info

    def _get_miner_prediction(
        self,
        synapse,
        miner_info: tuple[list[str], Ss58Address],
    ) -> str | None:
        """
        Prompt a miner module to generate an answer to the given question.

        Args:
            question: The question to ask the miner module.
            miner_info: A tuple containing the miner's connection information and key.

        Returns:
            The generated answer from the miner module, or None if the miner fails to generate an answer.
        """
        connection, miner_key = miner_info
        module_ip, module_port = connection
        client = ModuleClient(module_ip, int(module_port), self.key)
        try:
            # handles the communication with the miner
            current_time = datetime.now()
            miner_answer = dict()
            response = asyncio.run(
                client.call(
                    f'forward{synapse.class_name}',
                    miner_key,
                    {"synapse": synapse.dict()},
                    timeout=self.call_timeout,  #  type: ignore
                )
            )
            response = json.loads(response)
            # print(f'Response from miner: {response}')
            miner_answer['data'] = class_dict[response['class_name']](**response)
            process_time = datetime.now() - current_time
            miner_answer["process_time"] = process_time

        except Exception as e:
            log(f"Miner {module_ip}:{module_port} failed to generate an answer")
            print(e)
            miner_answer = None
        return miner_answer
    
    def get_miner_answer(self, modules_info, synapses):
        if not isinstance(synapses, list):
            synapses = [synapses] * len(modules_info)
        log(f"Selected the following miners: {modules_info.keys()}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            it = executor.map(lambda x: self._get_miner_prediction(x[0], x[1]), list(zip(synapses, modules_info.values())))
            answers = [*it]
            
        if not answers:
            log("No miner managed to give an answer")
            return None
        
        # print(f'miner answers: {answers}')
        
        return answers
        
    def get_pool_event_synapses(self, healthy_data: list[HealthCheckResponse]) -> list[PoolEventSynapse]:
        """
        Generate a prompt for the pool event check.

        Returns:
            The list of PoolEventSynapse.
        """
        synapses = []
        for miner_data in healthy_data:
            if miner_data is None or miner_data['data'] is None: continue
            miner_data = miner_data['data']
            days = (miner_data.time_completed - START_TIMESTAMP) // DAY_SECONDS
            random_pick = random.randint(0, days)
            start_date = random_pick * DAY_SECONDS + START_TIMESTAMP
            end_date = start_date + DAY_SECONDS
            pool_addr = random.choice(miner_data.pool_addresses)
            
            synapses.append(PoolEventSynapse(pool_address=pool_addr,
                                             start_datetime=start_date,
                                             end_datetime=end_date))

        return synapses
    
    def check_miner_answer_pool_event(self, miner_prompt: PoolEventSynapse, miner_answer: PoolEventResponse | None) -> bool:
        """
        Check if the miner answers are valid.
        
        Args:
            miner_prompt: The prompt for the miner modules.
            miner_answer: The generated answer from the miner module.
        """
        pool_address = miner_prompt.pool_address
        start_datetime = miner_prompt.start_datetime
        end_datetime = miner_prompt.end_datetime
        
        block_number_start, block_number_end = self.uniswap_fetcher_rs.get_block_number_range(start_datetime, end_datetime)
        
        miner_data = miner_answer.data
        if miner_data is None:
            return False
        ANSWER_CHECK_COUNT = 10
        correct_count = 0
        for _ in range(ANSWER_CHECK_COUNT):
            block_data = random.choice(miner_data)
            block_number = block_data.get("block_number", None)
            
            if block_number is None:
                return False
            if block_number < block_number_start or block_number > block_number_end:
                return False
            
            okay = 0
            block_data_from_pools = self.uniswap_fetcher_rs.get_pool_events_by_pool_addresses([pool_address], block_number, block_number)
            for block_data_of_pool in block_data_from_pools.get("data", []):
                if block_data_of_pool.get("transaction_hash") == block_data.get("transaction_hash"):
                    okay = 1
            correct_count += okay
        return correct_count / ANSWER_CHECK_COUNT

    def get_deviations(self, miner_prompt: SignalEventSynapse, miner_answer: SignalEventResponse):
        """
        Check if the miner answers are valid.
        
        Args:
            miner_prompt: The prompt for the miner modules.
            miner_answer: The generated answer from the miner module.
        """
        pool_address = miner_prompt.pool_address
        timestamp = miner_prompt.timestamp
        
        miner_data = miner_answer.data
        if miner_data is None:
            return False
        ground_truth = self.uniswap_fetcher_rs.get_signals_by_pool_address(pool_address, timestamp, '5m')
        
        return {
            'price': abs(ground_truth['price'] - miner_data.price),
            'liquidity': abs(ground_truth['liquidity'] - miner_data.liquidity),
            'volume': abs(ground_truth['volume'] - miner_data.volume),
        }

    def check_pool_event_accuracy(self, synapse: PoolEventSynapse, miner_answer: PoolEventResponse) -> float:
        """
        Score the generated answer against the validator's own answer.

        Args:
            miner_answer: The generated answer from the miner module.

        Returns:
            The score assigned to the miner's answer.
        """

        # Implement your custom scoring logic here
        if not miner_answer:
            return 0
        
        # count the number of correct entries

        accuracy_score = self.check_miner_answer_pool_event(synapse, miner_answer)
        
        accuracy_score = ((accuracy_score - 0.75) * 4) ** 3

        return accuracy_score

    def get_signal_event_synapse(self, healthy_data: list[HealthCheckResponse]) -> list[SignalEventSynapse]:
        """
        Generate a prompt for the signal event check.
        
        Returns:
            The list of SignalEventSynapse.
        """
        synapses = []
        for miner_data in healthy_data:
            if miner_data is None or miner_data['data'] is None: continue
            miner_data = miner_data['data']
            days = (miner_data.time_completed - START_TIMESTAMP) / (5 * 60)
            random_pick = random.randint(0, days)
            timestamp = random_pick * 300 + START_TIMESTAMP
            pool_addr = random.choice(miner_data.pool_addresses)
            
            synapses.append(SignalEventSynapse(pool_address=pool_addr,
                                               timestamp=timestamp))

        return synapses

    def score_pool_events(self, synapses, miner_results):
        """
        Score the miners based on their answers.
        
        Args:
            synapses: synapses for each miner
            miner_results: The results of the miner modules.
        """
        process_time_score = {}
        accuracy_score: dict[int, float] = {}
        
        for synapse, (key, miner_answer) in zip(synapses, miner_results):
            if not miner_answer:
                log(f"Skipping miner {key} that didn't answer")
                continue
            process_time_score[key] = miner_answer["process_time"].total_seconds()

            score = self.check_pool_event_accuracy(synapse, miner_answer['data'])
            time.sleep(0.5)
            # score has to be lower or eq to 1, as one is the best score, you can implement your custom logic
            assert score <= 1
            accuracy_score[key] = score
            
        print(f'process_time_score: {process_time_score}')
        if(len(process_time_score) == 0):
            return {}
        
        max_time = max(process_time_score.values())
        min_time = min(process_time_score.values())
        process_time_score = {key: 1 - 0.5 * (process_time - min_time) / (max_time - min_time + EPS) for key, process_time in process_time_score.items()}
        overall_score = {key: ((accuracy_score[key] + process_time_score[key]) / 2) for key in accuracy_score.keys()}
        
        return overall_score
    
    def score_health_check(self, miner_results):
        valid_miner_results = [(key, miner_answer) for key, miner_answer in miner_results if miner_answer is not None]
        if len(valid_miner_results) == 0:
            return {}
        
        timestamps = [miner_answer['data'].time_completed for key, miner_answer in valid_miner_results]
        mx_timestamp = max(timestamps)
        today_timestamp = datetime.today().timestamp()
        
        amount_score = {key: (miner_answer['data'].time_completed / mx_timestamp) for key, miner_answer in valid_miner_results}
        recency_score = {key: max(0, (10 * DAY_SECONDS + miner_answer['data'].time_completed - today_timestamp) / DAY_SECONDS / 10)
                          for key, miner_answer in valid_miner_results}
        
        return {key: amount_score[key] * 0.6 + recency_score[key] * 0.4 for key in amount_score.keys()}
    
    def score_signal_events(self, synapses, miner_results):
        """
        Score the miners based on their answers.
        
        Args:
            synapses: synapses for each miner
            miner_results: The results of the miner modules.
        """
        process_time_score = {}
        deviations: dict[int, float] = {}
        
        for synapse, (key, miner_answer) in zip(synapses, miner_results):
            if not miner_answer:
                log(f"Skipping miner {key} that didn't answer")
                continue
            process_time_score[key] = miner_answer["process_time"].total_seconds()

            deviations[key] = self.get_deviations(synapse, miner_answer['data'])
            
        print(f'process_time_score: {process_time_score}')
        if len(process_time_score) == 0:
            return {}
        
        max_time = max(process_time_score.values())
        min_time = min(process_time_score.values())
        process_time_score = {key: 1 - 0.5 * (process_time - min_time) / (max_time - min_time + EPS) for key, process_time in process_time_score.items()}
        
        def get_min_max_deviations(deviations: dict):
            min_deviations = {
                'price': min([deviation['price'] for key, deviation in deviations]),
                'liquidity': min([deviation['liquidity'] for key, deviation in deviations]),
                'volume': min([deviation['volume'] for key, deviation in deviations]),
            }
            max_deviations = {
                'price': max([deviation['price'] for deviation in deviations]),
                'liquidity': max([deviation['liquidity'] for deviation in deviations]),
                'volume': max([deviation['volume'] for deviation in deviations]),
            }
            return min_deviations, max_deviations

        def get_deviation_scores(deviations, min_deviations, max_deviations):
            return [key: {
                'price': 1 - (deviation['price'] - min_deviations['price']) / (max_deviations['price'] - min_deviations['price'] + EPS),
                'liquidity': 1 - (deviation['liquidity'] - min_deviations['liquidity']) / (max_deviations['liquidity'] - min_deviations['liquidity'] + EPS),
                'volume': 1 - (deviation['volume'] - min_deviations['volume']) / (max_deviations['volume'] - min_deviations['volume'] + EPS),
            }
            for key, deviation in deviations]
        
        def get_deviation_score(scores):
            return [{key: (score['price'] + score['liquidity'] + score['volume']) / 3} for key, score in scores]
        
        min_deviations, max_deviations = get_min_max_deviations(deviations)
        deviation_scores = get_deviation_scores(deviations, min_deviations, max_deviations)
        deviation_score = get_deviation_score(deviation_scores)
        
        overall_score = {key: ((deviation_score[key] + process_time_score[key]) / 2) for key in accuracy_score.keys()}
        
        return overall_score
    
    async def validate_step(
        self, velora_netuid: int, settings: ValidatorSettings
    ) -> None:
        """
        Perform a validation step.

        Generates questions based on the provided settings, prompts modules to generate answers,
        and scores the generated answers against the validator's own answers.

        Args:
            velora_netuid: The network UID of the subnet.
        """

        # retrive the miner information
        modules_info = self.retrieve_miner_information(velora_netuid)

        score_dict: dict[int, float] = {}
        # Check range
        health_check_synapse = HealthCheckSynapse()
        health_data = self.get_miner_answer(modules_info, health_check_synapse)
        miner_results_health_data = list(zip(modules_info.keys(), health_data))
        
        health_score = self.score_health_check(miner_results_health_data)
        valid_miner_infos = {key: modules_info[key] for key in health_score}
        log(f'valid_miner_infos: {valid_miner_infos}')
        
        if len(valid_miner_infos) == 0:
            log('No valid miners')
            return

        # Check pool events data
        pool_event_check_synapses = self.get_pool_event_synapses(health_data)
        pool_events = self.get_miner_answer(valid_miner_infos, pool_event_check_synapses)
        
        miner_results_pool_events = list(zip(valid_miner_infos.keys(), pool_events))

        pool_events_score = self.score_pool_events(pool_event_check_synapses, miner_results_pool_events)
        
        # Check signals
        signal_event_synapses = self.get_signal_event_synapse(health_data)
        signal_events = self.get_miner_answer(valid_miner_infos, signal_event_synapses)
        
        miner_results_signal_events = list(zip(valid_miner_infos.keys(), signal_events))
        
        signal_events_score = self.score_signal_events(signal_event_synapses, miner_results_signal_events)
        
        # Check prediction
        # prediction_synapse = PredictionSynapse()
        
        score_dict = {key: health_score[key] * 0.3 + pool_events_score[key] * 0.3 + signal_events_score[key] * 0.4 for key in modules_info.keys()}

        if not score_dict:
            log("No miner managed to give a valid answer")
            return None
        
        log(score_dict)

        # the blockchain call to set the weights
        _ = set_weights(settings, score_dict, self.netuid, self.client, self.key)

    def validation_loop(self, settings: ValidatorSettings) -> None:
        """
        Run the validation loop continuously based on the provided settings.

        Args:
            settings: The validator settings to use for the validation loop.
        """

        while True:
            start_time = time.time()
            _ = asyncio.run(self.validate_step(self.netuid, settings))

            elapsed = time.time() - start_time
            if elapsed < settings.iteration_interval:
                sleep_time = settings.iteration_interval - elapsed
                log(f"Sleeping for {sleep_time}")
                time.sleep(sleep_time)
