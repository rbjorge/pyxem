# -*- coding: utf-8 -*-
# Copyright 2016-2023 The pyXem developers
#
# This file is part of pyXem.
#
# pyXem is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# pyXem is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with pyXem.  If not, see <http://www.gnu.org/licenses/>.

import numpy as np
import math
from scipy.spatial.distance import cdist
from transforms3d.axangles import axangle2mat


def detector_to_fourier(k_xy, wavelength, camera_length):
    """Maps two-dimensional Cartesian coordinates in the detector plane to
    three-dimensional coordinates in reciprocal space, with origo in [000].

    The detector uses a left-handed coordinate system, while the reciprocal
    space uses a right-handed coordinate system.

    Parameters
    ----------
    k_xy : np.array()
        Cartesian coordinates in detector plane, in reciprocal Ångström.
    wavelength : float
        Electron wavelength in Ångström.
    camera_length : float
        Camera length in metres.

    Returns
    -------
    k : np.array()
        Array of Cartesian coordinates in reciprocal space relative to [000].

    """

    if k_xy.shape == (1,) and k_xy.dtype == "object":
        # From ragged array
        k_xy = k_xy

    # The calibrated positions of the diffraction spots are already the x and y
    # coordinates of the k vector on the Ewald sphere. The radius is given by
    # the wavelength. k_z is calculated courtesy of Pythagoras, then offset by
    # the Ewald sphere radius.

    k_z = np.sqrt(1 / (wavelength**2) - np.sum(k_xy**2, axis=1)) - 1 / wavelength

    # Stack the xy-vector and the z vector to get the full k
    k = np.hstack((k_xy, k_z[:, np.newaxis]))
    return k


def calculate_norms(z):
    """Calculates the norm of an array of cartesian vectors. For use with map().

    Parameters
    ----------
    z : np.array()
        Array of cartesian vectors.

    Returns
    -------
    norms : np.array()
        Array of vector norms.
    """
    return np.linalg.norm(z, axis=1)


def calculate_norms_ragged(z):
    """Calculates the norm of an array of cartesian vectors. For use with map()
    when applied to a ragged array.

    Parameters
    ----------
    z : np.array()
        Array of cartesian vectors.

    Returns
    -------
    norms : np.array()
        Array of vector norms.
    """
    norms = []
    for i in z:
        norms.append(np.linalg.norm(i))
    return np.asarray(norms)


def filter_vectors_ragged(z, min_magnitude, max_magnitude):
    """Filters the diffraction vectors to accept only those with magnitudes
    within a user specified range.

    Parameters
    ----------
    min_magnitude : float
        Minimum allowed vector magnitude.
    max_magnitude : float
        Maximum allowed vector magnitude.

    Returns
    -------
    filtered_vectors : np.array()
        Diffraction vectors within allowed magnitude tolerances.
    """
    # Calculate norms
    norms = []
    for i in z:
        norms.append(np.linalg.norm(i))
    norms = np.asarray(norms)
    # Filter based on norms
    norms[norms < min_magnitude] = 0
    norms[norms > max_magnitude] = 0
    filtered_vectors = z[np.where(norms)]

    return filtered_vectors


def filter_vectors_edge_ragged(z, x_threshold, y_threshold):
    """Filters the diffraction vectors to accept only those not within a user
    specified proximity to detector edge.

    Parameters
    ----------
    x_threshold : float
        Maximum x-coordinate in calibrated units.
    y_threshold : float
        Maximum y-coordinate in calibrated units.

    Returns
    -------
    filtered_vectors : np.array()
        Diffraction vectors within allowed tolerances.
    """
    # Filter x / y coordinates
    z[np.absolute(z.T[0]) > x_threshold] = 0
    z[np.absolute(z.T[1]) > y_threshold] = 0
    filtered_vectors = z[np.where(z.T[0])]

    return filtered_vectors


def normalize_or_zero(v):
    """Normalize `v`, or return the vector directly if it has zero length.

    Parameters
    ----------
    v : np.array()
        Single vector or array of vectors to be normalized.
    """
    norms = np.linalg.norm(v, axis=-1)
    nonzero_mask = norms > 0
    if np.any(nonzero_mask):
        v[nonzero_mask] /= norms[nonzero_mask].reshape(-1, 1)


def get_rotation_matrix_between_vectors(from_v1, from_v2, to_v1, to_v2):
    """Calculates the rotation matrix from one pair of vectors to the other.
    Handles multiple to-vectors from a single from-vector.

    Find `R` such that `v_to = R @ v_from`.

    Parameters
    ----------
    from_v1, from_v2 : np.array()
        Vector to rotate _from_.
    to_v1, to_v2 : np.array()
        Nx3 array of vectors to rotate _to_.

    Returns
    -------
    R : np.array()
        Nx3x3 list of rotation matrices between the vector pairs.
    """
    # Find normals to rotate around
    plane_normal_from = np.cross(from_v1, from_v2, axis=-1)
    plane_normal_to = np.cross(to_v1, to_v2, axis=-1)
    plane_common_axes = np.cross(plane_normal_from, plane_normal_to, axis=-1)

    # Try to remove normals from degenerate to-planes by replacing them with
    # the rotation axes between from and to vectors.
    to_degenerate = np.isclose(np.sum(np.abs(plane_normal_to), axis=-1), 0.0)
    plane_normal_to[to_degenerate] = np.cross(from_v1, to_v1[to_degenerate], axis=-1)
    to_degenerate = np.isclose(np.sum(np.abs(plane_normal_to), axis=-1), 0.0)
    plane_normal_to[to_degenerate] = np.cross(from_v2, to_v2[to_degenerate], axis=-1)

    # Normalize the axes used for rotation
    normalize_or_zero(plane_normal_to)
    normalize_or_zero(plane_common_axes)

    # Create rotation from-plane -> to-plane
    common_valid = ~np.isclose(np.sum(np.abs(plane_common_axes), axis=-1), 0.0)
    angles = get_angle_cartesian_vec(
        np.broadcast_to(plane_normal_from, plane_normal_to.shape), plane_normal_to
    )
    R1 = np.empty((angles.shape[0], 3, 3))
    if np.any(common_valid):
        R1[common_valid] = np.array(
            [
                axangle2mat(axis, angle, is_normalized=True)
                for axis, angle in zip(
                    plane_common_axes[common_valid], angles[common_valid]
                )
            ]
        )
    R1[~common_valid] = np.identity(3)

    # Rotate from-plane into to-plane
    rot_from_v1 = np.matmul(R1, from_v1)
    rot_from_v2 = np.matmul(R1, from_v2)

    # Create rotation in the now common plane

    # Find the average angle
    angle1 = get_angle_cartesian_vec(rot_from_v1, to_v1)
    angle2 = get_angle_cartesian_vec(rot_from_v2, to_v2)
    angles = 0.5 * (angle1 + angle2)
    # Negate angles where the rotation where the rotation axis points the
    # opposite way of the to-plane normal. Einsum gives list of dot
    # products.
    neg_angle_mask = (
        np.einsum("ij,ij->i", np.cross(rot_from_v1, to_v1, axis=-1), plane_normal_to)
        < 0
    )
    np.negative(angles, out=angles, where=neg_angle_mask)

    # To-plane normal still the same
    R2 = np.array(
        [
            axangle2mat(axis, angle, is_normalized=True)
            for axis, angle in zip(plane_normal_to, angles)
        ]
    )

    # Total rotation is the combination of to plane R1 and in plane R2
    R = np.matmul(R2, R1)

    return R


def get_npeaks(found_peaks):
    """Returns the number of entries in a list. For use with map().

    Parameters
    ----------
    found_peaks : np.array()
        Array of found peaks.

    Returns
    -------
    len : int
        The number of peaks in the array.
    """
    return len(found_peaks)


def get_angle_cartesian_vec(a, b):
    """Compute the angles between two lists of vectors in a cartesian
    coordinate system.

    Parameters
    ----------
    a, b : np.array()
        The two lists of directions to compute the angle between in Nx3 float
        arrays.

    Returns
    -------
    angles : np.array()
        List of angles between `a` and `b` in radians.
    """
    if a.shape != b.shape:
        raise ValueError(
            "The shape of a {} and b {} must be the same.".format(a.shape, b.shape)
        )

    denom = np.linalg.norm(a, axis=-1) * np.linalg.norm(b, axis=-1)
    denom_nonzero = denom != 0.0
    angles = np.zeros(a.shape[0])
    angles[denom_nonzero] = np.arccos(
        np.clip(
            np.sum(a[denom_nonzero] * b[denom_nonzero], axis=-1) / denom[denom_nonzero],
            -1.0,
            1.0,
        )
    ).ravel()
    return angles


def get_angle_cartesian(a, b):
    """Compute the angle between two vectors in a cartesian coordinate system.

    Parameters
    ----------
    a, b : array-like with 3 floats
        The two directions to compute the angle between.

    Returns
    -------
    angle : float
        Angle between `a` and `b` in radians.
    """
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return math.acos(max(-1.0, min(1.0, np.dot(a, b) / denom)))


def filter_vectors_near_basis(vectors, basis, distance=None):
    """
    Filter an array of vectors to only the list of closest vectors
    to some set of basis vectors.  Only vectors within some `distance`
    are considered.  If no vector is within the `distance` np.nan is
    returned for that vector.

    Parameters
    ----------
    vectors: array-like
        A two dimensional array of vectors where each row identifies a new vector

    basis: array-like
        A two dimensional array of vectors where each row identifies a vector.

    Returns
    -------
    closest_vectors: array-like
        An array of vectors which are the closest to the basis considered.
    """
    if len(vectors) == 0:
        vectors = np.empty(basis.shape)
        vectors[:, :] = np.nan
        return vectors
    distance_mat = cdist(vectors, basis)
    closest_index = np.argmin(distance_mat, axis=0)
    min_distance = distance_mat[closest_index, np.arange(len(basis), dtype=int)]
    closest_vectors = vectors[closest_index]
    if distance is not None:
        if closest_vectors.dtype == int:
            closest_vectors = closest_vectors.astype(float)

        closest_vectors[min_distance > distance, :] = np.nan
    return closest_vectors
