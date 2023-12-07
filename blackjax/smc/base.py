# Copyright 2020- The Blackjax Authors.
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
from typing import Callable, NamedTuple, Optional

import jax
import jax.numpy as jnp

from blackjax.types import Array, ArrayLikeTree, ArrayTree, PRNGKey


class SMCState(NamedTuple):
    """State of the SMC sampler.

    Particles must be a ArrayTree, each leave represents a variable from the posterior,
    being an array of size `(n_particles, ...)`.
    Examples (three particles):
        - Single univariate posterior:
            [ Array([[1.], [1.2], [3.4]]) ]
        - Single bivariate  posterior:
            [Array([[1,2], [3,4], [5,6]])]
        - Two variables, each univariate:
            [ Array([[1.], [1.2], [3.4]]),
            Array([[50.], [51], [55]]) ]
        - Two variables, first one bivariate, second one 4-variate:
            [ Array([[1., 2.], [1.2, 0.5], [3.4, 50]]),
            Array([[50., 51., 52., 51], [51., 52., 52. ,54.], [55., 60, 60, 70]])]
    """

    particles: ArrayTree
    weights: Array


class SMCInfo(NamedTuple):
    """Additional information on the tempered SMC step.

    ancestors: Array
        The index of the particles proposed by the MCMC pass that were selected
        by the resampling step.
    log_likelihood_increment: float
        The log-likelihood increment due to the current step of the SMC algorithm.
    update_info: NamedTuple
        Additional information returned by the update function.
    """

    ancestors: Array
    log_likelihood_increment: float
    update_info: NamedTuple


def init(particles: ArrayLikeTree):
    # Infer the number of particles from the size of the leading dimension of
    # the first leaf of the inputted PyTree.
    num_particles = jax.tree_util.tree_flatten(particles)[0][0].shape[0]
    weights = jnp.ones(num_particles) / num_particles
    return SMCState(particles, weights)


def step(
    rng_key: PRNGKey,
    state: SMCState,
    update_fn: Callable,
    weight_fn: Callable,
    resample_fn: Callable,
    num_resampled: Optional[int] = None,
) -> tuple[SMCState, SMCInfo]:
    """General SMC sampling step.

    `update_fn` here corresponds to the Markov kernel $M_{t+1}$, and `weight_fn`
    corresponds to the potential function $G_t$. We first use `update_fn` to
    generate new particles from the current ones, weigh these particles using
    `weight_fn` and resample them with `resample_fn`.

    The `update_fn` and `weight_fn` functions must be batched by the called either
    using `jax.vmap` or `jax.pmap`.

    In Feynman-Kac terms, the algorithm goes roughly as follows:

    .. code::

        M_t: update_fn
        G_t: weight_fn
        R_t: resample_fn
        idx = R_t(weights)
        x_t = x_tm1[idx]
        x_{t+1} = M_t(x_t)
        weights = G_t(x_{t+1})

    Parameters
    ----------
    rng_key
        Key used to generate pseudo-random numbers.
    state
        Current state of the SMC sampler: particles and their respective
        log-weights
    update_fn
        Function that takes an array of keys and particles and returns
        new particles.
    weight_fn
        Function that assigns a weight to the particles.
    resample_fn
        Function that resamples the particles.
    num_resampled
        The number of particles to resample. This can be used to implement
        Waste-Free SMC :cite:p:`dau2020waste`, in which case we resample a number :math:`M<N`
        of particles, and the update function is in charge of returning
        :math:`N` samples.

    Returns
    -------
    new_particles
        An array that contains the new particles generated by this SMC step.
    info
        An `SMCInfo` object that contains extra information about the SMC
        transition.

    """
    updating_key, resampling_key = jax.random.split(rng_key, 2)

    num_particles = state.weights.shape[0]

    if num_resampled is None:
        num_resampled = num_particles

    resampling_idx = resample_fn(resampling_key, state.weights, num_resampled)
    particles = jax.tree_map(lambda x: x[resampling_idx], state.particles)

    keys = jax.random.split(updating_key, num_resampled)
    particles, update_info = update_fn(keys, particles)

    log_weights = weight_fn(particles)
    logsum_weights = jax.scipy.special.logsumexp(log_weights)
    normalizing_constant = logsum_weights - jnp.log(num_particles)
    weights = jnp.exp(log_weights - logsum_weights)

    return SMCState(particles, weights), SMCInfo(
        resampling_idx, normalizing_constant, update_info
    )
