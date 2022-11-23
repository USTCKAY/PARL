#   Copyright (c) 2022. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Functions to compute V-trace off-policy actor critic targets,
which used in IMAPLA algorithm.

The following code is mainly referenced and copied from:
https://github.com/deepmind/scalable_agent/blob/master/vtrace.py

For details and theory see:

"Espeholt L, Soyer H, Munos R, et al. Impala: Scalable distributed 
deep-rl with importance weighted actor-learner 
architectures[J]. arXiv preprint arXiv:1802.01561, 2018."

"""

import collections
import paddle

VTraceReturns = collections.namedtuple('VTraceReturns',
                                       ['vs', 'pg_advantages'])


# Make sure no gradients backpropagated through the returned values.
@paddle.no_grad()
def from_importance_weights(behaviour_actions_log_probs,
                            target_actions_log_probs,
                            discounts,
                            rewards,
                            values,
                            bootstrap_value,
                            clip_rho_threshold=1.0,
                            clip_pg_rho_threshold=1.0,
                            name='vtrace_from_logits'):
    r"""V-trace for softmax policies.

    Calculates V-trace actor critic targets for softmax polices as described in

    "IMPALA: Scalable Distributed Deep-RL with
    Importance Weighted Actor-Learner Architectures"
    by Espeholt, Soyer, Munos et al.

    Target policy refers to the policy we are interested in improving and
    behaviour policy refers to the policy that generated the given
    rewards and actions.

    In the notation used throughout documentation and comments, T refers to the
    time dimension ranging from 0 to T-1. B refers to the batch size and
    NUM_ACTIONS refers to the number of actions.

    Args:
      behaviour_actions_log_probs: A float32 tensor of shape [T, B] of
        log-probabilities of actions in behaviour policy.
      target_policy_logits: A float32 tensor of shape [T, B] of
        log-probabilities of actions in target policy.
      discounts: A float32 tensor of shape [T, B] with the discount encountered
        when following the behaviour policy.
      rewards: A float32 tensor of shape [T, B] with the rewards generated by
        following the behaviour policy.
      values: A float32 tensor of shape [T, B] with the value function estimates
        wrt. the target policy.
      bootstrap_value: A float32 of shape [B] with the value function estimate at
        time T.
      clip_rho_threshold: A scalar float32 tensor with the clipping threshold for
        importance weights (rho) when calculating the baseline targets (vs).
        rho^bar in the paper.
      clip_pg_rho_threshold: A scalar float32 tensor with the clipping threshold
        on rho_s in \rho_s \delta log \pi(a|x) (r + \gamma v_{s+1} - V(x_s)).
      name: The name scope that all V-trace operations will be created in.

    Returns:
      A VTraceReturns namedtuple (vs, pg_advantages) where:
        vs: A float32 tensor of shape [T, B]. Can be used as target to
          train a baseline (V(x_t) - vs_t)^2.
        pg_advantages: A float32 tensor of shape [T, B]. Can be used as the
          advantage in the calculation of policy gradients.
    """

    rank = len(behaviour_actions_log_probs.shape)  # Usually 2.
    assert len(target_actions_log_probs.shape) == rank
    assert len(values.shape) == rank
    assert len(bootstrap_value.shape) == (rank - 1)
    assert len(discounts.shape) == rank
    assert len(rewards.shape) == rank

    # log importance sampling weights.
    # V-trace performs operations on rhos in log-space for numerical stability.
    log_rhos = target_actions_log_probs - behaviour_actions_log_probs

    rhos = paddle.exp(log_rhos)
    if clip_rho_threshold is not None:
        clipped_rhos = paddle.clip(rhos, max=clip_rho_threshold)
    else:
        clipped_rhos = rhos

    cs = paddle.clip(rhos, max=1.0)

    # Append bootstrapped value to get [v1, ..., v_t+1]
    values_t_plus_1 = paddle.concat(
        [values[1:], paddle.unsqueeze(bootstrap_value, 0)], axis=0)

    # \delta_s * V
    deltas = clipped_rhos * (rewards + discounts * values_t_plus_1 - values)

    acc = paddle.zeros_like(bootstrap_value)
    result = []
    for t in range(discounts.shape[0] - 1, -1, -1):
        acc = deltas[t] + discounts[t] * cs[t] * acc
        result.append(acc)
    result.reverse()
    vs_minus_v_xs = paddle.stack(result)

    # Add V(x_s) to get v_s.
    vs = paddle.add(vs_minus_v_xs, values)

    # Advantage for policy gradient.
    vs_t_plus_1 = paddle.concat(
        [vs[1:], paddle.unsqueeze(bootstrap_value, 0)], axis=0)

    if clip_pg_rho_threshold is not None:
        clipped_pg_rhos = paddle.clip(rhos, max=clip_pg_rho_threshold)
    else:
        clipped_pg_rhos = rhos

    pg_advantages = (
        clipped_pg_rhos * (rewards + discounts * vs_t_plus_1 - values))

    return VTraceReturns(vs=vs, pg_advantages=pg_advantages)
