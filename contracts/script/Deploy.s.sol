// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Script} from "forge-std/Script.sol";
import {MockUSDC} from "../src/MockUSDC.sol";
import {TaskEscrow} from "../src/TaskEscrow.sol";

contract Deploy is Script {
    function run() external returns (MockUSDC token, TaskEscrow escrow) {
        uint256 key = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(key);
        address verifier = vm.envAddress("VERIFIER_ADDRESS");
        // Only explicitly configured development chains are accepted by the script.
        require(block.chainid == vm.envUint("LOCAL_CHAIN_ID"), "unexpected development chain");
        require(block.chainid != 1, "mainnet forbidden");
        vm.startBroadcast(key);
        token = new MockUSDC(deployer);
        escrow = new TaskEscrow(address(token), deployer);
        escrow.grantRole(escrow.VERIFIER_ROLE(), verifier);
        vm.stopBroadcast();
    }
}
